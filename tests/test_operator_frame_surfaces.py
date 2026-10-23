"""Independent operator-frame behavior on real source and platform entrypoints."""
from contextlib import ExitStack,contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Client,Final,Framework,Registry,Settings,ToolCall,compile_program
from chaos.documentation import Documentation
from chaos.execution_operators import (OperatorInvokeFrame,OperatorCompareFrame,OperatorEqualFrame,
    OperatorBinaryFrame,OperatorInplaceFrame,OperatorOperationFrame)
from chaos.ide_language import analyze
from chaos.language import Evaluator,Native
from chaos.modules import STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import parse_program,format_program
from chaos.server import make_server
from chaos.source_format import format_source
from test_resource_protocol_frame_surfaces import RESOURCE_FRAMES,PREVIOUS_FRAMES
ROOT=Path(__file__).resolve().parents[1]
OPERATOR_FRAMES=(OperatorInvokeFrame,OperatorCompareFrame,OperatorEqualFrame,OperatorBinaryFrame,
    OperatorInplaceFrame,OperatorOperationFrame)
ALL_FRAMES=RESOURCE_FRAMES+PREVIOUS_FRAMES+OPERATOR_FRAMES
OPERATOR_NAMES={cls.__name__ for cls in OPERATOR_FRAMES}
ALL_NAMES={cls.__name__ for cls in ALL_FRAMES}
SOURCE='let operatorResult=do{\nvar operatorEvents=[];\nclass OperatorLeft {\n fn __iadd__(self,other){operatorEvents=append(operatorEvents,"inplace");NotImplemented}\n fn __add__(self,other){operatorEvents=append(operatorEvents,"add");42}\n}\nclass OperatorRight(OperatorLeft){fn __radd__(self,other){operatorEvents=append(operatorEvents,"reverse");NotImplemented}}\nclass OperatorValue {\n fn __eq__(self,other){operatorEvents=append(operatorEvents,"equal");other==42}\n fn __lt__(self,other){operatorEvents=append(operatorEvents,"less");42<other}\n fn __len__(self){operatorEvents=append(operatorEvents,"length");2}\n fn __contains__(self,other){operatorEvents=append(operatorEvents,"contains");other==42}\n fn __bool__(self){operatorEvents=append(operatorEvents,"bool");true}\n fn __neg__(self){operatorEvents=append(operatorEvents,"neg");-42}\n}\nvar operatorAmount=OperatorLeft();operatorAmount+=OperatorRight();\nlet operatorValues=[operatorAmount,[OperatorValue()]==[42],OperatorValue()<43,length(OperatorValue()),contains(OperatorValue(),42),bool(OperatorValue()),-OperatorValue()];\n[operatorValues,operatorEvents]\n};\nimport "std/context" as R;\nvar resourceEvents=[];\nlet resourceTrace=context_var("resourceTrace","root");let selectedResourceContext=context();\nlet selectedContextValues=selectedResourceContext.run(async fn(){\n resourceTrace.set("inside");await sleep(0);\n let bound=with context_bind([{variable:resourceTrace,value:"bound"}]) as binding{resourceTrace.get()};\n [resourceTrace.get(),bound]\n});\nclass ResourcePlain{\n fn __enter__(self){resourceEvents=append(resourceEvents,["enter",resourceTrace.get()]);20}\n fn __exit__(self,error){resourceEvents=append(resourceEvents,["exit",resourceTrace.get(),error==null]);false}\n}\nclass ResourceAsync{\n async fn __aenter__(self){await sleep(0);resourceEvents=append(resourceEvents,["aenter",resourceTrace.get()]);22}\n async fn __aexit__(self,error){await sleep(0);resourceEvents=append(resourceEvents,["aexit",resourceTrace.get(),error==null]);null}\n}\nlet customResources=selectedResourceContext.run(async fn(){with ResourcePlain() as first{async with ResourceAsync() as second{first+second}}});\n@R.contextmanager\nfn* resourcePlain(value){try{yield value}finally{resourceEvents=append(resourceEvents,["sync",value])}}\n@R.asynccontextmanager\nasync fn* resourceAsync(value){try{await sleep(0);yield value}finally{await sleep(0);resourceEvents=append(resourceEvents,["async",value])}}\nasync fn resourceNote(value){await sleep(0);resourceEvents=append(resourceEvents,["anote",value]);null}\nlet mixedResources=async with R.AsyncExitStack() as stack{\n let first=stack.enter_context(resourcePlain(20));let second=await stack.enter_async_context(resourceAsync(22));\n stack.callback(fn(value){resourceEvents=append(resourceEvents,["callback",value]);null},1);\n stack.push(fn(error){resourceEvents=append(resourceEvents,["push",error==null]);false});\n stack.push_async_callback(resourceNote,2);\n stack.push_async_exit(async fn(error){await sleep(0);resourceEvents=append(resourceEvents,["apush",error==null]);null});\n let moved=stack.pop_all();await moved.aclose();await moved.aclose();first+second\n};\nlet plainResources=exit_stack();plainResources.callback(fn(){resourceEvents=append(resourceEvents,["plain"]);null});plainResources.close();plainResources.close();\nfn resourceRecording(value){let detached=exit_stack();detached.callback(fn(){resourceEvents=append(resourceEvents,["recorded",resourceTrace.get()]);null});tool("text.normalize","  resource  ");42}\nlet resourceRecorded=await record_skill(resourceRecording,null);\nvar pendingResourceErrors=[];\nlet suppressedResource=with exit_stack() as stack{\n stack.push(fn(error){pendingResourceErrors=append(pendingResourceErrors,error.value);true});\n stack.push(fn(error){throw "cleanup"});throw "body"\n};\nlet resourceResult={context:[selectedContextValues,resourceTrace.get(),selectedResourceContext[resourceTrace]],custom:customResources,mixed:mixedResources,recorded:[resourceRecorded.output,length(mode_info(resourceRecorded.skill).actions)],handled:[suppressedResource,pendingResourceErrors],events:resourceEvents};\nvar streamEvents=[];\nclass Cursor{\n var position=0;\n fn __iter__(self){self}\n fn __next__(self){self.position=self.position+1;streamEvents=append(streamEvents,self.position);\n  if self.position<=3{{done:false,value:self.position}}else{{done:true,value:"finished"}}}\n fn __close__(self){streamEvents=append(streamEvents,"cursorClosed");null}\n}\nlet cursor=iter(Cursor());let decisive=any(fn(value){value==2},cursor);\nlet tail=collect(cursor);close(cursor);close(cursor);\nlet eager=[x+y for x in [20] for y in [20,22] if y>20];\nlet record={key:key+key for key in ["a","b"]};\nlet distinct=length({x%2 for x in [1,2,3]});\nlet lazy=(x*2 for x in [20,21]);\nlet lazyValues=[next(lazy).value,next(lazy).value,next(lazy).done];\nasync fn* asyncSource(){try{yield 20;yield 21}finally{streamEvents=append(streamEvents,"asyncSourceClosed")}}\nlet asynchronous=[x*2 async for x in asyncSource()];\nlet asyncLazy=await acollect(async (x*2 for x in [20,21]));\nclass AsyncCursor{\n var position=20;\n fn __aiter__(self){self}\n async fn __anext__(self){await sleep(0);if self.position<22{let value=self.position;self.position=self.position+1;{done:false,value:value}}else{{done:true,value:84}}}\n async fn __aclose__(self){await sleep(0);streamEvents=append(streamEvents,"asyncCursorClosed");null}\n}\nlet asyncCursor=aiter(AsyncCursor());let operation=anext(asyncCursor);let cold=task_info(operation).state;\nlet firstAsync=await operation;let tailAsync=await acollect(asyncCursor);await aclose(asyncCursor);await aclose(asyncCursor);\nfn* delegated(){yield from [40,42]}\nlet delegation=delegated();let firstDelegated=next(delegation).value;close(delegation);\nlet consumers=[map(fn(x){x+1},[40,41]),filter(fn(x){x>40},[40,41]),fold(fn(a,x){a+x},0,[20,22]),\n apply(fn(a,b){a+b},[20,22]),(fn(a,b){a+b})(*[20,22]),length(tuple([1,2])),length(set_of([1,1,2])),all(fn(x){x>0},[1,2])];\nlet iterationResult={borrow:[decisive,tail],eager:eager,record:record,setCount:distinct,lazy:lazyValues,\n asynchronous:asynchronous,asyncLazy:asyncLazy,customAsync:[cold,firstAsync.value,tailAsync],\n delegated:firstDelegated,consumers:consumers,events:streamEvents};\nvar masks=[];\nwith handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,c.kind)}}]) as outer{\n with handler_bind([{predicate:fn(c){true},handler:fn(c){\n  with handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,"new")}}]) as fresh{signal_condition(condition("nested"))};\n  masks=append(masks,"inner")\n }}]) as inner{signal_condition(condition("root"))}\n};\nlet inline=with restart_bind([{name:"inline",test:fn(c){c!=null&&c.kind=="missing"},function:fn(x){x+1}}]) as scope{\n let offered=compute_restarts(condition("missing"));\n let chosen=find_restart("inline",condition("missing"));\n [length(offered),restart_info(chosen).mode,invoke_restart(chosen,[40])]\n};\nvar cleanup=[];\nlet captured=multiple_value_list(fn(){restart_case(async fn(){\n try{with handler_bind([{predicate:async fn(c){await sleep(0);true},handler:async fn(c){await sleep(0);invoke_restart("use",[c.["data"].fallback])}}]) as policy{\n  error_condition(condition("missing",data={fallback:41}))\n }}finally{cleanup=append(cleanup,"cleanup")}\n},[{name:"use",function:async fn(x){await sleep(0);cleanup=append(cleanup,"restart");return_values(x+1,find_restart("use")==null)}}])});\nlet unhandled=try{error_condition(condition("unhandled"))}catch e{e.code};\nlet noHost=try{invoke_debugger(condition("explicit"))}catch e{e.code};\nlet recovered=[inline,captured,masks,cleanup,unhandled,noHost];\nimport "std/context" as C;\nvar events=[];\n@C.asynccontextmanager\nasync fn* resource(){try{await sleep(0);yield {value:41}}finally{await sleep(0);events=append(events,"closed")}}\nasync fn* remaining(){try{yield 0}finally{events=append(events,"streamClosed")}}\nfn plan(s){s.input}\nasync fn execute(p,s){\n let held=remaining();await anext(held);\n with cancel_shield() as shield{async with task_group() as group{\n  async with resource() as {value:value}{let tag=prompt_tag("package");with prompt_scope(tag) as scope{\n   control_barrier(async fn(){\n    let answer=call_escape(async fn(exit){dynamic_wind(fn(){null},async fn(){await sleep(0);exit(value+1)},fn(){null})});\n    call_prompt(tag,fn(){abort_prompt(tag,answer)},fn(result){result})\n   })\n  }}\n }}\n}\nfn verify(s){{done:true,context:{retired:length(events)},output:s.evidence[0]}}\nlet worker=bot("typed recovery deployment",plan,execute,verify,context={retired:0});\nlet pending=worker(null);let before=events;\nlet actual=await pending;\n[actual.output,events,actual.status,actual.state.context,before,recovered,iterationResult,resourceResult,operatorResult]\n'
EXPECTED=[42, ['closed', 'streamClosed'], 'achieved', {'retired': 2}, [], [[1, 'inline', 41], [42, True], ['new', 'nested', 'inner', 'root'], ['cleanup', 'restart'], 'condition_error', 'condition_error'], {'borrow': [True, [3]], 'eager': [42], 'record': {'a': 'aa', 'b': 'bb'}, 'setCount': 2, 'lazy': [40, 42, True], 'asynchronous': [40, 42], 'asyncLazy': [40, 42], 'customAsync': ['created', 20, [21]], 'delegated': 40, 'consumers': [[41, 42], [41], 42, 42, 42, 2, 2, True], 'events': [1, 2, 3, 4, 'cursorClosed', 'asyncSourceClosed', 'asyncCursorClosed']}, {'context': [['inside', 'bound'], 'root', 'inside'], 'custom': 42, 'mixed': 42, 'recorded': [42, 1], 'handled': [None, ['cleanup']], 'events': [['enter', 'inside'], ['aenter', 'inside'], ['aexit', 'inside', True], ['exit', 'inside', True], ['apush', True], ['anote', 2], ['push', True], ['callback', 1], ['async', 22], ['sync', 20], ['plain'], ['recorded', 'root']]}, [[42, True, True, 2, True, True, -42], ['inplace', 'reverse', 'add', 'equal', 'less', 'length', 'contains', 'bool', 'neg']]]
DOCUMENTED='var calls=[];\nclass Left {\n fn __iadd__(self,other){calls=append(calls,"inplace");NotImplemented}\n fn __add__(self,other){calls=append(calls,"add");42}\n fn __eq__(self,other){calls=append(calls,"equal");other==42}\n}\nclass Right(Left){fn __radd__(self,other){calls=append(calls,"reverse");NotImplemented}}\nvar answer=Left();answer+=Right();\n[answer,[Left()]==[42],calls]\n'
DOCUMENTED_EXPECTED=[42, True, ['inplace', 'reverse', 'add', 'equal']]
FUNCTION='fn(values){class Amount{var value=0;fn __init__(self,value){self.value=value;null}fn __iadd__(self,other){self.value+other}}var amount=Amount(values[0]);amount+=values[1];amount}'

@contextmanager
def observe_operator_frames():
    seen={'frames':set(),'steps':{},'phases':{},'cursors':set(),'live':[],'methods':set(),'operations':set()}
    with ExitStack()as changes:
        for cls in ALL_FRAMES:
            original=cls.step
            def observed(frame,cursor,value,error,original=original):
                name=type(frame).__name__;seen['frames'].add(name);seen['cursors'].add(id(cursor))
                seen['steps'][name]=seen['steps'].get(name,0)+1
                seen['phases'].setdefault(name,set()).add(frame.phase)
                if type(frame)in OPERATOR_FRAMES:
                    seen['live'].append(frame)
                    if type(frame)is OperatorInvokeFrame:seen['methods'].add(frame.method)
                    elif type(frame)is not OperatorEqualFrame:seen['operations'].add(frame.operation)
                return original(frame,cursor,value,error)
            changes.enter_context(patch.object(cls,'step',observed))
        yield seen

class OperatorFrameSurfaces(unittest.TestCase):
    def run_source(self,source,*,mode='source',registry=None,capabilities=None):
        with Framework(':memory:',settings=Settings(':memory:'),registry=registry)as app:
            project=app.create_project('operator-frames',source=source,capabilities=['text.normalize']if capabilities is None else capabilities)
            run=app.start(project['id'],project['version'],None,mode=mode)
            result=run.result(15);events=[];after=0
            while True:
                page=app.events(project['id'],run_id=run.id,after=after,limit=300);events.extend(page['items'])
                if page['nextCursor']is None:break
                after=page['nextCursor']
            return result,events

    def assert_all_frames(self,seen):
        self.assertEqual(seen['frames'],ALL_NAMES)
        self.assertEqual(len(seen['frames']),59)
        self.assertTrue(all(n>0 for n in seen['steps'].values()))
        self.assertGreater(len(seen['cursors']),1)
        self.assertTrue({'__iadd__','__add__','__radd__','__eq__','__lt__','__len__','__contains__','__bool__','__neg__'}<=seen['methods'])

    def test_source_graph_and_deployment_observe_all_fifty_nine_real_step_classes(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),observe_operator_frames()as seen:
                result,events=self.run_source(SOURCE,mode=mode)
                self.assertEqual(result,EXPECTED);self.assert_all_frames(seen)
                kinds=('bot.scope.opened','bot.scope.closed','bot.result.committed','bot.decision.created')
                self.assertEqual({kind:sum(e['type']==kind for e in events)for kind in kinds},dict(zip(kinds,(1,1,1,3))))
                self.assertEqual(sum(e['type']=='tool.completed'and e['payload'].get('tool')=='text.normalize'for e in events),1)

    def test_both_ir_dialects_and_program_roundtrip_keep_operator_results_and_order(self):
        for version in (1,2):
            ir=parse_program(SOURCE,language_version=version);formatted=format_program(ir)
            self.assertEqual(parse_program(formatted,language_version=version)['semanticHash'],ir['semanticHash'])
            definition={'kind':'program','entry':'main.chaos','files':{'main.chaos':formatted},'languageVersions':{'main.chaos':version}}
            with self.subTest(version=version),observe_operator_frames()as seen:
                self.assertEqual(self.run_source('let p=program_from('+json.dumps(definition)+');await p(null)')[0],EXPECTED)
                self.assert_all_frames(seen)

    def test_code_program_macros_and_compilation_use_the_same_operator_frames(self):
        program={'kind':'program','entry':'main.chaos','files':{'main.chaos':'let build='+FUNCTION+';build([40,2])'},'languageVersions':{'main.chaos':2}}
        sources=['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build([40,2])',
            'let p=program_from('+json.dumps(program)+');await p(null)',
            'macro calculate(factory,value){quote{unquote(factory)+unquote(value)}};class Amount{fn __add__(self,x){40+x}};calculate!(Amount(),2)']
        for source in sources:
            with self.subTest(source=source),observe_operator_frames()as seen:
                self.assertEqual(self.run_source(source)[0],42)
                self.assertTrue({'OperatorInvokeFrame','OperatorBinaryFrame','OperatorOperationFrame'}<=seen['frames'])
        for target in ('interpreter','auto','auto-flow','auto-control','portable'):
            chosen='auto-flow'if target=='portable'else target
            source=FUNCTION.replace('fn(values','fn build(values')+';let f=compile_function(build,target="'+chosen+'");f([40,2])'
            with self.subTest(target=target),observe_operator_frames()as seen:
                if target=='portable':
                    with patch('chaos.native_flow.compile_flow',side_effect=BackendUnavailable('fixture portable')):self.assertEqual(self.run_source(source)[0],42)
                else:self.assertEqual(self.run_source(source)[0],42)
                self.assertTrue({'OperatorInvokeFrame','OperatorInplaceFrame'}<=seen['frames'])

    def test_real_agent_tool_team_bot_callbacks_keep_context_and_exactly_once_effects(self):
        registry=Registry();effects=[];provider_steps=[]
        @registry.tool('fixture.operator.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.operator.provider')
        def provider(request,context):
            context.checkpoint();provider_steps.append((request.input,request.step))
            return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'operator team','entry':'work','roles':[{'id':'work','parent':None,'capabilities':['fixture.operator.audit'],'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.operator.audit']}}}]}
        source='''let trace=context_var("trace","root");
class Amount{var value=0;fn __init__(self,value){self.value=value;null}
 fn __add__(self,x){tool("fixture.operator.audit",["add",self.value,x,trace.get()]);self.value+x}
 fn __iadd__(self,x){tool("fixture.operator.audit",["inplace",self.value,x,trace.get()]);self.value+x}}
async fn work(value:Int){with context_bind([{variable:trace,value:"agent"}])as scope{Amount(value)+1}}
agent helper{provider:"fixture.operator.provider",tools:["fixture.operator.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.operator.audit"])]);
async fn role(value){with context_bind([{variable:trace,value:"team"}])as scope{var amount=Amount(value);amount+=21;amount}}
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(plan,state){let first=await bound(plan);let second=await crew(first);[first,second,trace.get()]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("operator work",fn(state){state.input},execute,verify);(await worker(20)).output'''.replace('TEAM',json.dumps(team))
        with observe_operator_frames()as seen:result,events=self.run_source(source,registry=registry,capabilities=['fixture.operator.audit'])
        self.assertEqual(result,[21,42,'root'])
        self.assertEqual(effects,[['add',20,1,'agent'],['inplace',21,21,'team']])
        self.assertEqual(provider_steps,[(20,1),(20,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1)
        self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)
        self.assertEqual(sum(e['type']=='tool.completed'for e in events),2)
        self.assertTrue({'OperatorInvokeFrame','OperatorInplaceFrame','BotCommitFrame'}<=seen['frames'])

    def test_actual_pending_operator_frame_survives_real_effect_and_cancellation_drains(self):
        for cancelled in (False,True):
            entered=threading.Event();release=threading.Event();ended=threading.Event();calls=[];registry=Registry()
            @registry.provider('fixture.operator.pending')
            def pending(request,context):
                calls.append(request.input);entered.set()
                try:
                    while not release.wait(.005):context.checkpoint()
                    context.checkpoint();return Final(request.input+1)
                finally:ended.set()
            source='agent worker{provider:"fixture.operator.pending",tools:[],maxSteps:1};class Amount{fn __add__(self,x){worker(x)}};async fn work(){Amount()+41};await work()'
            with self.subTest(cancelled=cancelled),Framework(':memory:',registry=registry,settings=Settings(':memory:'))as app:
                project=app.create_project('pending-operator',source=source,capabilities=[])
                with observe_operator_frames()as seen:
                    run=app.start(project['id'],project['version'],None,mode='source')
                    try:
                        self.assertTrue(entered.wait(4))
                        active=[frame for frame in seen['live']if type(frame)is OperatorInvokeFrame and frame.method=='__add__']
                        self.assertTrue(active);self.assertEqual(active[-1].phase,'invoke');self.assertEqual(active[-1].args,[41]);self.assertIsNotNone(active[-1].receiver)
                        if cancelled:
                            run.cancel();self.assertEqual(run.wait(5)['status'],'cancelled');self.assertTrue(ended.is_set())
                        else:release.set();self.assertEqual(run.result(5),42);self.assertTrue(ended.is_set())
                    finally:release.set();run.cancel()
                self.assertEqual(calls,[41]);self.assertIn('OperatorInvokeFrame',seen['frames'])

    def test_cold_task_generator_contracts_and_errors_do_not_start_fallback(self):
        cases=[('''var calls=0;class C{async fn __add__(self,x){calls=calls+1;x+1}fn* __neg__(self){yield 42}async fn* __abs__(self){yield 41}}
let c=C();let pending=c+41;let before=calls;[before,await pending,calls,collect(-c),await acollect(abs(c))]''',[0,42,1,[42],[41]]),
            ('''var calls=[];class C{fn __iadd__(self,x){calls=append(calls,"inplace");throw "stop"}fn __add__(self,x){calls=append(calls,"add");42}}
var c=C();let original=c;let reason=try{c+=1}catch e{e.value};[reason,c==original,calls]''',['stop',True,['inplace']]),
            ('''var calls=0;class C{fn __eq__(self,x){calls=calls+1;calls==1}}let a=[C()];let b=[42];let result=[a,a]==[b,b];[result,calls]''',[False,2])]
        for source,expected in cases:
            with self.subTest(source=source),observe_operator_frames()as seen:
                self.assertEqual(self.run_source(source)[0],expected)
                self.assertIn('OperatorInvokeFrame',seen['frames'])

    def test_http_sdk_and_cli_keep_the_exact_operator_deployment_source(self):
        with Framework(':memory:',settings=Settings(':memory:'))as app,tempfile.TemporaryDirectory()as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]));project=client.create_project('operator-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_operator_frames()as seen:self.assertEqual(client.start(project['id'],project['version'],None,mode='graph').result(15),EXPECTED)
                self.assert_all_frames(seen);self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE)
                env={k:v for k,v in os.environ.items()if not any(word in k.upper()for word in('MODEL','PROVIDER','KEY','TOKEN'))};env['CHAOS_CONFIG']=str(Path(folder)/'settings.json');env['PYTHONPATH']=str(ROOT)
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command=='fmt':self.assertEqual(parse_program(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                    if command=='ir':self.assertEqual(json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)

    def test_inert_editor_catalogue_and_exact_documented_operator_example(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring invoked a callback')):
            report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash'])
        self.assertEqual(len([v for v in Evaluator().env.values.values()if type(v)is Native]),335)
        self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
        self.assertIn(DOCUMENTED.strip(),Documentation().page('operators')['markdown'])
        with observe_operator_frames()as seen:self.assertEqual(self.run_source(DOCUMENTED)[0],DOCUMENTED_EXPECTED)
        self.assertTrue(OPERATOR_NAMES<=seen['frames'])

if __name__=='__main__':unittest.main()
