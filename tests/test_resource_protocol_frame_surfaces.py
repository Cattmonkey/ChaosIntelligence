"""Actual dynamic context and resource protocols on shared language surfaces."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, Settings, ToolCall, compile_program
from chaos.documentation import Documentation
from chaos.execution_iteration import (IterationAcquireFrame, IterationInvokeFrame,
    IterationAwaitFrame, IterationStepFrame, IterationCloseFrame, IterationCollectFrame,
    IterationConsumeFrame, IterationDelegateFrame, IterationResolveFrame,
    StreamResumeFrame, StreamCloseFrame)
from chaos.execution_comprehensions import ComprehensionFrame, ComprehensionWalkFrame
from chaos.execution_conditions import (ConditionRequestFrame, RecoveryCallbackFrame,
    ConditionSignalFrame, ConditionDebuggerFrame, RestartComputeFrame, RestartInvokeFrame,
    RestartCaseFrame)
from chaos.execution_bot import (BotRunFrame, BotHostFrame, BotCallbackFrame,
    BotPhaseFrame, BotPhaseBodyFrame, BotCloseOwnedFrame, BotCleanupFrame,
    BotDecisionFrame, BotDrainFrame, BotOutcomeFrame, BotCommitFrame)
from chaos.execution_control import (ControlCallbackFrame, PromptHandlerFrame,
    PromptCallFrame, ControlBarrierFrame, DynamicWindFrame)
from chaos.execution_frames import (ContextFrame, PromptScopeFrame, CancelShieldFrame,
    TaskGroupFrame, GroupBodyFrame)
from chaos.ide_language import analyze
from chaos.language import Evaluator, Native, LanguageError
from chaos.modules import STDLIB
from chaos.native_jit import BackendUnavailable
from chaos.native_specs import all_specs
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.source_format import format_source

ROOT = Path(__file__).resolve().parents[1]
ITERATION_FRAMES = (IterationAcquireFrame, IterationInvokeFrame, IterationAwaitFrame,
    IterationStepFrame, IterationCloseFrame, IterationCollectFrame, IterationConsumeFrame,
    IterationDelegateFrame, IterationResolveFrame, StreamResumeFrame, StreamCloseFrame,
    ComprehensionFrame, ComprehensionWalkFrame)
PREVIOUS_FRAMES = (ContextFrame, PromptScopeFrame, CancelShieldFrame, TaskGroupFrame,
    GroupBodyFrame, ControlCallbackFrame, PromptHandlerFrame, PromptCallFrame,
    ControlBarrierFrame, DynamicWindFrame, BotRunFrame, BotHostFrame, BotCallbackFrame,
    BotPhaseFrame, BotPhaseBodyFrame, BotCloseOwnedFrame, BotCleanupFrame,
    BotDecisionFrame, BotDrainFrame, BotOutcomeFrame, BotCommitFrame,
    ConditionRequestFrame, RecoveryCallbackFrame, ConditionSignalFrame,
    ConditionDebuggerFrame, RestartComputeFrame, RestartInvokeFrame, RestartCaseFrame)
ITERATION_NAMES = {cls.__name__ for cls in ITERATION_FRAMES}
ALL_NAMES = ITERATION_NAMES | {cls.__name__ for cls in PREVIOUS_FRAMES}

from chaos.execution_context_protocols import (ContextRunFrame, ResourceAwaitFrame,
    ResourceInvokeFrame, GeneratorContextOperationFrame, GeneratorContextExitFrame,
    ResourceAcquireFrame, ResourceExitCallFrame, ResourceExitEntryFrame,
    StackRequestFrame, StackExecuteFrame, StackUnwindFrame, StackFinalizeScopeFrame)
RESOURCE_FRAMES = (ContextRunFrame, ResourceAwaitFrame, ResourceInvokeFrame,
    GeneratorContextOperationFrame, GeneratorContextExitFrame, ResourceAcquireFrame,
    ResourceExitCallFrame, ResourceExitEntryFrame, StackRequestFrame, StackExecuteFrame,
    StackUnwindFrame, StackFinalizeScopeFrame)
PREVIOUS_FRAMES = PREVIOUS_FRAMES + ITERATION_FRAMES
ALL_NAMES = {cls.__name__ for cls in RESOURCE_FRAMES + PREVIOUS_FRAMES}

SOURCE = 'import "std/context" as R;\nvar resourceEvents=[];\nlet resourceTrace=context_var("resourceTrace","root");let selectedResourceContext=context();\nlet selectedContextValues=selectedResourceContext.run(async fn(){\n resourceTrace.set("inside");await sleep(0);\n let bound=with context_bind([{variable:resourceTrace,value:"bound"}]) as binding{resourceTrace.get()};\n [resourceTrace.get(),bound]\n});\nclass ResourcePlain{\n fn __enter__(self){resourceEvents=append(resourceEvents,["enter",resourceTrace.get()]);20}\n fn __exit__(self,error){resourceEvents=append(resourceEvents,["exit",resourceTrace.get(),error==null]);false}\n}\nclass ResourceAsync{\n async fn __aenter__(self){await sleep(0);resourceEvents=append(resourceEvents,["aenter",resourceTrace.get()]);22}\n async fn __aexit__(self,error){await sleep(0);resourceEvents=append(resourceEvents,["aexit",resourceTrace.get(),error==null]);null}\n}\nlet customResources=selectedResourceContext.run(async fn(){with ResourcePlain() as first{async with ResourceAsync() as second{first+second}}});\n@R.contextmanager\nfn* resourcePlain(value){try{yield value}finally{resourceEvents=append(resourceEvents,["sync",value])}}\n@R.asynccontextmanager\nasync fn* resourceAsync(value){try{await sleep(0);yield value}finally{await sleep(0);resourceEvents=append(resourceEvents,["async",value])}}\nasync fn resourceNote(value){await sleep(0);resourceEvents=append(resourceEvents,["anote",value]);null}\nlet mixedResources=async with R.AsyncExitStack() as stack{\n let first=stack.enter_context(resourcePlain(20));let second=await stack.enter_async_context(resourceAsync(22));\n stack.callback(fn(value){resourceEvents=append(resourceEvents,["callback",value]);null},1);\n stack.push(fn(error){resourceEvents=append(resourceEvents,["push",error==null]);false});\n stack.push_async_callback(resourceNote,2);\n stack.push_async_exit(async fn(error){await sleep(0);resourceEvents=append(resourceEvents,["apush",error==null]);null});\n let moved=stack.pop_all();await moved.aclose();await moved.aclose();first+second\n};\nlet plainResources=exit_stack();plainResources.callback(fn(){resourceEvents=append(resourceEvents,["plain"]);null});plainResources.close();plainResources.close();\nfn resourceRecording(value){let detached=exit_stack();detached.callback(fn(){resourceEvents=append(resourceEvents,["recorded",resourceTrace.get()]);null});tool("text.normalize","  resource  ");42}\nlet resourceRecorded=await record_skill(resourceRecording,null);\nvar pendingResourceErrors=[];\nlet suppressedResource=with exit_stack() as stack{\n stack.push(fn(error){pendingResourceErrors=append(pendingResourceErrors,error.value);true});\n stack.push(fn(error){throw "cleanup"});throw "body"\n};\nlet resourceResult={context:[selectedContextValues,resourceTrace.get(),selectedResourceContext[resourceTrace]],custom:customResources,mixed:mixedResources,recorded:[resourceRecorded.output,length(mode_info(resourceRecorded.skill).actions)],handled:[suppressedResource,pendingResourceErrors],events:resourceEvents};\nvar streamEvents=[];\nclass Cursor{\n var position=0;\n fn __iter__(self){self}\n fn __next__(self){self.position=self.position+1;streamEvents=append(streamEvents,self.position);\n  if self.position<=3{{done:false,value:self.position}}else{{done:true,value:"finished"}}}\n fn __close__(self){streamEvents=append(streamEvents,"cursorClosed");null}\n}\nlet cursor=iter(Cursor());let decisive=any(fn(value){value==2},cursor);\nlet tail=collect(cursor);close(cursor);close(cursor);\nlet eager=[x+y for x in [20] for y in [20,22] if y>20];\nlet record={key:key+key for key in ["a","b"]};\nlet distinct=length({x%2 for x in [1,2,3]});\nlet lazy=(x*2 for x in [20,21]);\nlet lazyValues=[next(lazy).value,next(lazy).value,next(lazy).done];\nasync fn* asyncSource(){try{yield 20;yield 21}finally{streamEvents=append(streamEvents,"asyncSourceClosed")}}\nlet asynchronous=[x*2 async for x in asyncSource()];\nlet asyncLazy=await acollect(async (x*2 for x in [20,21]));\nclass AsyncCursor{\n var position=20;\n fn __aiter__(self){self}\n async fn __anext__(self){await sleep(0);if self.position<22{let value=self.position;self.position=self.position+1;{done:false,value:value}}else{{done:true,value:84}}}\n async fn __aclose__(self){await sleep(0);streamEvents=append(streamEvents,"asyncCursorClosed");null}\n}\nlet asyncCursor=aiter(AsyncCursor());let operation=anext(asyncCursor);let cold=task_info(operation).state;\nlet firstAsync=await operation;let tailAsync=await acollect(asyncCursor);await aclose(asyncCursor);await aclose(asyncCursor);\nfn* delegated(){yield from [40,42]}\nlet delegation=delegated();let firstDelegated=next(delegation).value;close(delegation);\nlet consumers=[map(fn(x){x+1},[40,41]),filter(fn(x){x>40},[40,41]),fold(fn(a,x){a+x},0,[20,22]),\n apply(fn(a,b){a+b},[20,22]),(fn(a,b){a+b})(*[20,22]),length(tuple([1,2])),length(set_of([1,1,2])),all(fn(x){x>0},[1,2])];\nlet iterationResult={borrow:[decisive,tail],eager:eager,record:record,setCount:distinct,lazy:lazyValues,\n asynchronous:asynchronous,asyncLazy:asyncLazy,customAsync:[cold,firstAsync.value,tailAsync],\n delegated:firstDelegated,consumers:consumers,events:streamEvents};\nvar masks=[];\nwith handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,c.kind)}}]) as outer{\n with handler_bind([{predicate:fn(c){true},handler:fn(c){\n  with handler_bind([{predicate:fn(c){true},handler:fn(c){masks=append(masks,"new")}}]) as fresh{signal_condition(condition("nested"))};\n  masks=append(masks,"inner")\n }}]) as inner{signal_condition(condition("root"))}\n};\nlet inline=with restart_bind([{name:"inline",test:fn(c){c!=null&&c.kind=="missing"},function:fn(x){x+1}}]) as scope{\n let offered=compute_restarts(condition("missing"));\n let chosen=find_restart("inline",condition("missing"));\n [length(offered),restart_info(chosen).mode,invoke_restart(chosen,[40])]\n};\nvar cleanup=[];\nlet captured=multiple_value_list(fn(){restart_case(async fn(){\n try{with handler_bind([{predicate:async fn(c){await sleep(0);true},handler:async fn(c){await sleep(0);invoke_restart("use",[c.["data"].fallback])}}]) as policy{\n  error_condition(condition("missing",data={fallback:41}))\n }}finally{cleanup=append(cleanup,"cleanup")}\n},[{name:"use",function:async fn(x){await sleep(0);cleanup=append(cleanup,"restart");return_values(x+1,find_restart("use")==null)}}])});\nlet unhandled=try{error_condition(condition("unhandled"))}catch e{e.code};\nlet noHost=try{invoke_debugger(condition("explicit"))}catch e{e.code};\nlet recovered=[inline,captured,masks,cleanup,unhandled,noHost];\nimport "std/context" as C;\nvar events=[];\n@C.asynccontextmanager\nasync fn* resource(){try{await sleep(0);yield {value:41}}finally{await sleep(0);events=append(events,"closed")}}\nasync fn* remaining(){try{yield 0}finally{events=append(events,"streamClosed")}}\nfn plan(s){s.input}\nasync fn execute(p,s){\n let held=remaining();await anext(held);\n with cancel_shield() as shield{async with task_group() as group{\n  async with resource() as {value:value}{let tag=prompt_tag("package");with prompt_scope(tag) as scope{\n   control_barrier(async fn(){\n    let answer=call_escape(async fn(exit){dynamic_wind(fn(){null},async fn(){await sleep(0);exit(value+1)},fn(){null})});\n    call_prompt(tag,fn(){abort_prompt(tag,answer)},fn(result){result})\n   })\n  }}\n }}\n}\nfn verify(s){{done:true,context:{retired:length(events)},output:s.evidence[0]}}\nlet worker=bot("typed recovery deployment",plan,execute,verify,context={retired:0});\nlet pending=worker(null);let before=events;\nlet actual=await pending;\n[actual.output,events,actual.status,actual.state.context,before,recovered,iterationResult,resourceResult]\n'
EXPECTED = [42, ['closed', 'streamClosed'], 'achieved', {'retired': 2}, [], [[1, 'inline', 41], [42, True], ['new', 'nested', 'inner', 'root'], ['cleanup', 'restart'], 'condition_error', 'condition_error'], {'borrow': [True, [3]], 'eager': [42], 'record': {'a': 'aa', 'b': 'bb'}, 'setCount': 2, 'lazy': [40, 42, True], 'asynchronous': [40, 42], 'asyncLazy': [40, 42], 'customAsync': ['created', 20, [21]], 'delegated': 40, 'consumers': [[41, 42], [41], 42, 42, 42, 2, 2, True], 'events': [1, 2, 3, 4, 'cursorClosed', 'asyncSourceClosed', 'asyncCursorClosed']}, {'context': [['inside', 'bound'], 'root', 'inside'], 'custom': 42, 'mixed': 42, 'recorded': [42, 1], 'handled': [None, ['cleanup']], 'events': [['enter', 'inside'], ['aenter', 'inside'], ['aexit', 'inside', True], ['exit', 'inside', True], ['apush', True], ['anote', 2], ['push', True], ['callback', 1], ['async', 22], ['sync', 20], ['plain'], ['recorded', 'root']]}]
DOCUMENTED = 'import "std/context" as C;\nvar released=[];\n@C.contextmanager\nfn* resource(value){try{yield value}finally{released=append(released,value)}}\nlet trace=context_var("trace","root");let selected=context();\nlet answer=selected.run(fn(){\n trace.set("worker");\n with C.ExitStack() as stack{\n  let first=stack.enter_context(resource(20));let second=stack.enter_context(resource(22));first+second\n }\n});\n[answer,released,trace.get(),selected[trace]]\n'
DOCUMENTED_EXPECTED = [42, [22, 20], 'root', 'worker']
FUNCTION = 'fn(values){let selected=context();selected.run(fn(){let stack=exit_stack();stack.callback(fn(){null});stack.close();values[0]+values[1]+2})}'


@contextmanager
def observe_resource_frames():
    seen={'frames':set(),'owners':set(),'cursors':set(),'steps':{},'phases':{},'operations':set()}
    with ExitStack() as observations:
        for cls in RESOURCE_FRAMES + PREVIOUS_FRAMES:
            original=cls.step
            def observed(frame,cursor,value,error,original=original):
                name=type(frame).__name__;seen['frames'].add(name)
                seen['owners'].add(type(cursor.owner).__name__);seen['cursors'].add(id(cursor))
                seen['steps'][name]=seen['steps'].get(name,0)+1
                seen['phases'].setdefault(name,set()).add(frame.phase)
                if type(frame) is StackRequestFrame:seen['operations'].add(frame.request.operation)
                return original(frame,cursor,value,error)
            observations.enter_context(patch.object(cls,'step',observed))
        yield seen


class ResourceProtocolFrameSurfaces(unittest.TestCase):
    def run_source(self, source, *, registry=None, capabilities=None, mode='source'):
        with Framework(':memory:', registry=registry, settings=Settings(':memory:')) as app:
            p = app.create_project('resource-protocols', source=source,
                capabilities=['text.normalize'] if capabilities is None else capabilities)
            run = app.start(p['id'], p['version'], None, mode=mode)
            result = run.result(15)
            events, after = [], 0
            while True:
                page = app.events(p['id'], run_id=run.id, after=after, limit=300)
                events.extend(page['items'])
                if page['nextCursor'] is None:break
                after = page['nextCursor']
            return result, events

    def assert_all_frames(self, seen):
        self.assertEqual(seen['frames'], ALL_NAMES, seen)
        self.assertEqual(seen['owners'], {'Evaluator'})
        self.assertTrue(all(count > 0 for count in seen['steps'].values()))
        self.assertGreater(len(seen['cursors']), 1)

        self.assertEqual(seen['operations'], {'enter_context','enter_async_context','callback','push','push_async_callback','push_async_exit','pop_all','close','aclose'})

    def test_source_and_graph_execute_all_fifty_three_real_frame_classes(self):
        for mode in ('source', 'graph'):
            with self.subTest(mode=mode), observe_resource_frames() as seen:
                self.assertEqual(self.run_source(SOURCE, mode=mode)[0], EXPECTED)
            self.assert_all_frames(seen)


    def test_ir_v1_and_v2_roundtrip_share_every_real_frame(self):
        for version in (1, 2):
            ir = parse_program(SOURCE, language_version=version)
            formatted = format_program(ir)
            self.assertEqual(parse_program(formatted, language_version=version)['semanticHash'], ir['semanticHash'])
            definition = {'kind': 'program', 'entry': 'main.chaos',
                'files': {'main.chaos': formatted}, 'languageVersions': {'main.chaos': version}}
            source = 'let p=program_from('+json.dumps(definition)+');await p(null)'
            with self.subTest(version=version), observe_resource_frames() as seen:
                self.assertEqual(self.run_source(source)[0], EXPECTED)
            self.assert_all_frames(seen)


    def test_code_program_macros_and_compile_targets_share_resource_callbacks(self):
        module = {'kind':'program','entry':'main.chaos',
            'files':{'main.chaos':FUNCTION.replace('fn(values','fn build(values')+';export{build};'},
            'languageVersions':{'main.chaos':2}}
        program = {'kind':'program','entry':'main.chaos',
            'files':{'main.chaos':'let build='+FUNCTION+';build([20,20])'},
            'languageVersions':{'main.chaos':2}}
        sources = ['let build=function_from(code_from('+json.dumps(FUNCTION)+'));build([20,20])',
            'let M=module_from(program_from('+json.dumps(module)+'));M.build([20,20])',
            'let p=program_from('+json.dumps(program)+');await p(null)']
        required = {'ContextRunFrame','StackRequestFrame','StackExecuteFrame','StackUnwindFrame'}
        for source in sources:
            with self.subTest(source=source), observe_resource_frames() as seen:
                self.assertEqual(self.run_source(source)[0], 42)
            self.assertTrue(required <= seen['frames'], seen)
        macro='macro own(manager,value){quote{with unquote(manager) as stack{unquote(value)}}};let stack=42;own!(exit_stack(),stack)'
        with observe_resource_frames() as seen:self.assertEqual(self.run_source(macro)[0],42)
        self.assertTrue({'ResourceAcquireFrame','ResourceExitEntryFrame','StackUnwindFrame'} <= seen['frames'],seen)
        for target in ('interpreter','auto','auto-flow','auto-control','portable'):
            selected = 'auto-flow' if target == 'portable' else target
            source = FUNCTION.replace('fn(values','fn build(values')+';let compiled=compile_function(build,target="'+selected+'");compiled([20,20])'
            with self.subTest(target=target), observe_resource_frames() as seen:
                if target == 'portable':
                    with patch('chaos.native_flow.compile_flow', side_effect=BackendUnavailable('fixture portable')):
                        self.assertEqual(self.run_source(source)[0], 42)
                else:self.assertEqual(self.run_source(source)[0], 42)
            self.assertTrue(required <= seen['frames'], seen)


    def test_http_sdk_and_cli_execute_exact_same_deployment_source(self):
        with Framework(':memory:',settings=Settings(':memory:')) as app,tempfile.TemporaryDirectory() as folder:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                p=client.create_project('resource-http',source=SOURCE,capabilities=['text.normalize'])
                with observe_resource_frames() as seen:
                    self.assertEqual(client.start(p['id'],p['version'],None,mode='graph').result(12),EXPECTED)
                self.assert_all_frames(seen)
                self.assertEqual(client.get_project(p['id'])['source'],SOURCE)
                path=Path(folder)/'main.chaos';path.write_text(SOURCE)
                env={k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('MODEL','PROVIDER','KEY','TOKEN'))}
                env['CHAOS_CONFIG']=str(Path(folder)/'settings.json');env['PYTHONPATH']=str(ROOT)
                for command in ('check','ir','fmt','run'):
                    result=subprocess.run([sys.executable,'-B','-m','chaos',command,str(path)],
                        cwd=folder,env=env,capture_output=True,text=True,timeout=15)
                    self.assertEqual(result.returncode,0,result.stderr)
                    if command=='run':self.assertEqual(json.loads(result.stdout),EXPECTED)
                    if command=='fmt':self.assertEqual(parse_program(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
                    if command=='ir':self.assertEqual(json.loads(result.stdout)['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:server.shutdown();server.server_close();thread.join(2)


    def test_inert_catalogue_documented_and_deployment_examples(self):
        with patch.object(Evaluator,'_s_invoke',side_effect=AssertionError('authoring executed callback')):
            report=analyze(DOCUMENTED);formatted=format_source(DOCUMENTED);compiled=compile_program(DOCUMENTED)
        self.assertEqual(report['diagnostics'],[]);self.assertTrue(compiled)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(DOCUMENTED)['semanticHash'])
        self.assertEqual(len([v for v in Evaluator().env.values.values() if type(v) is Native]),335)
        self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
        self.assertIn(DOCUMENTED.strip(),Documentation().page('resource-stacks')['markdown'])
        with observe_resource_frames() as seen:
            self.assertEqual(self.run_source(formatted)[0],DOCUMENTED_EXPECTED)
        self.assertTrue({'ContextRunFrame','GeneratorContextOperationFrame','GeneratorContextExitFrame','StackUnwindFrame'} <= seen['frames'],seen)
        with observe_resource_frames() as seen:
            result,events=self.run_source(SOURCE)
        self.assertEqual(result,EXPECTED);self.assert_all_frames(seen)
        counts={kind:sum(e['type']==kind for e in events) for kind in
            ('bot.scope.opened','bot.scope.closed','bot.result.committed','bot.decision.created')}
        self.assertEqual(counts,{'bot.scope.opened':1,'bot.scope.closed':1,'bot.result.committed':1,'bot.decision.created':3})

        self.assertEqual(sum(e['type']=='tool.completed' and e['payload'].get('tool')=='text.normalize' for e in events),1)


    def test_actual_agent_tool_team_bot_context_and_cleanup_effects_execute_once(self):
        registry,effects,provider_steps=Registry(),[],[]
        @registry.tool('fixture.resource.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return value
        @registry.provider('fixture.resource.provider')
        def provider(request,context):
            context.checkpoint();provider_steps.append((request.input,request.step))
            return ToolCall('work',request.input) if request.step==1 else Final(request.history[-1]['output'])
        team={'kind':'team','name':'resource team','entry':'work','roles':[
            {'id':'work','parent':None,'capabilities':['fixture.resource.audit'],
             'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':['fixture.resource.audit']}}}]}
        source='''import "std/context" as C;let trace=context_var("trace","root");
@C.asynccontextmanager
async fn* held(value){try{await sleep(0);yield value}finally{tool("fixture.resource.audit",["release",value,trace.get()])}}
async fn work(value:Int){let selected=context();selected.run(async fn(){trace.set("agent");
 async with C.AsyncExitStack() as stack{let n=await stack.enter_async_context(held(value));tool("fixture.resource.audit",["agent",n,trace.get()]);n+1}})}
agent helper{provider:"fixture.resource.provider",tools:["fixture.resource.audit"]};
let bound=agent_bind(helper,[agent_tool("work",work,input_type="Int",output_type="Int",capabilities=["fixture.resource.audit"])]);
async fn role(value){let selected=context();selected.run(async fn(){trace.set("team");with exit_stack() as stack{
 stack.callback(fn(){tool("fixture.resource.audit",["team-close",value,trace.get()]);null});
 tool("fixture.resource.audit",["team",value,trace.get()]);value*2}})}
let crew=team_bind(team_from(TEAM),{work:role});
async fn execute(plan,state){let first=await bound(plan);let second=await crew(first);[first,second,trace.get()]}
fn verify(state){{done:true,context:state.context,output:state.evidence[0]}}
let worker=bot("resource work",fn(state){state.input},execute,verify);(await worker(20)).output'''.replace('TEAM',json.dumps(team))
        with observe_resource_frames() as seen:
            result,events=self.run_source(source,registry=registry,capabilities=['fixture.resource.audit'])
        self.assertEqual(result,[21,42,'root'])
        self.assertEqual(effects,[['agent',20,'agent'],['release',20,'agent'],['team',21,'team'],['team-close',21,'team']])
        self.assertEqual(provider_steps,[(20,1),(20,2)])
        self.assertEqual(sum(e['type']=='agent.language_tool'for e in events),1)
        self.assertEqual(sum(e['type']=='team.role.started'for e in events),1)
        self.assertTrue({'ContextRunFrame','ResourceAwaitFrame','GeneratorContextOperationFrame',
            'GeneratorContextExitFrame','StackExecuteFrame','StackUnwindFrame','BotCommitFrame'}<=seen['frames'],seen)

    def test_selected_context_cold_results_borrowed_task_and_multiple_values(self):
        cases=[('''let v=context_var("x",0);let c=context();async fn f(){v.get()}
let t=c.run(fn(){v.set(42);f()});[task_info(t).state,await t,v.get()]''',['created',42,0]),
            ('''let v=context_var("x",0);let c=context();c.run(fn(){v.set(42)});
let g=c.run(fn*(){yield v.get()});[type_of(g),next(g).value,v.get()]''',['Generator',42,0]),
            ('''let v=context_var("x",0);async fn child(){v.get()}let borrowed=child();let before=task_info(borrowed).state;
class Borrow{fn __aenter__(self){borrowed}async fn __aexit__(self,error){null}}
let c=context();let answer=c.run(async fn(){v.set(42);let n=async with Borrow() as value{value};[n,v.get()]});
[before,answer,c.get(v),v.get(),task_info(borrowed).state]''',['created',[0,42],42,0,'completed']),
            ('let selected=context();multiple_value_list(fn(){selected.run(fn(){return_values(20,22)})})',[20,22])]
        for source,expected in cases:
            with self.subTest(source=source),observe_resource_frames() as seen:
                self.assertEqual(self.run_source(source)[0],expected)
            self.assertIn('ContextRunFrame',seen['frames'])

    def test_group_failure_drains_owned_cleanup_before_outer_exit(self):
        entered,ended,calls=threading.Event(),threading.Event(),[]
        def effect(kind,payload,cancel):
            name=payload['name']
            if name=='inner':
                calls.append(name);entered.set()
                try:self.assertTrue(cancel.wait(4))
                finally:ended.set()
                return None
            if name=='bad':self.assertTrue(entered.wait(4));raise LanguageError('child',code='resource_group')
            if name=='outer':self.assertTrue(ended.is_set());calls.append(name);return None
            self.fail(name)
        source='''async fn bad(){tool("bad",null)}
try{async with task_group() as group{group.spawn(bad());async with exit_stack(true) as stack{
 stack.callback(fn(){tool("outer",null)});stack.push_async_callback(async fn(){tool("inner",null)});42}}}catch error{error.code}'''
        evaluator=Evaluator(effect_handler=effect,tool_handler=lambda name,value:effect('tool',{'name':name,'value':value},threading.Event()))
        with observe_resource_frames() as seen:self.assertEqual(evaluator.run(source),'resource_group')
        self.assertEqual(calls,['inner','outer']);self.assertTrue(ended.is_set())
        self.assertTrue({'ResourceAwaitFrame','StackUnwindFrame','StackExecuteFrame'}<=seen['frames'],seen)
        self.assertEqual(evaluator.suspension.stack_entries,0)

    def test_recorder_finalizes_owned_stack_before_success_or_abort_without_replay(self):
        for failed in (False,True):
            effects=[];registry=Registry()
            @registry.tool('fixture.resource.audit')
            def audit(value,context):context.checkpoint();effects.append(value);return value
            source='''fn action(input){let stack=exit_stack();stack.callback(fn(){tool("fixture.resource.audit","release")});tool("fixture.resource.audit",input)}
let captured=await record_skill(action,42);[captured.output,map(fn(a){a.input},mode_info(captured.skill).actions)]'''
            expected=[42,[42,'release']];wanted=[42,'release']
            if failed:
                source='''fn action(input){let stack=exit_stack();stack.callback(fn(){tool("fixture.resource.audit","release")});
fn* owner(){with stack as s{try{yield 42}finally{tool("fixture.resource.audit","body")}}}
let stream=owner();next(stream);throw "original"}
let error=try{await record_skill(action)}catch error{error.value};tool("fixture.resource.audit","outside");error'''
                expected='original';wanted=['body','release','outside']
            with self.subTest(failed=failed),Framework(':memory:',registry=registry,settings=Settings(':memory:')) as app:
                p=app.create_project('record-resources',source=source,capabilities=['fixture.resource.audit'])
                with observe_resource_frames() as seen:self.assertEqual(app.start(p['id'],p['version'],None).result(8),expected)
                self.assertEqual(effects,wanted);self.assertIn('StackFinalizeScopeFrame',seen['frames'])
                if failed:
                    row=app.modes(p['id'])['items'][0]
                    self.assertEqual(row['status'],'incomplete');self.assertEqual(len(row['checkpoint']['receipts']),2)

    def test_concurrent_context_alias_busy_and_later_reuse_restore_caller(self):
        source='''let v=context_var("x",0);let selected=context();let ready=channel(1);let gate=channel();
async fn held(){v.set(42);await channel_send(ready,0);await channel_receive(gate);v.get()}
async fn run(){selected.run(held)}let pending=run();spawn(pending);await channel_receive(ready);
let busy=try{selected.run(fn(){0})}catch error{error.code};await channel_send(gate,0);let answer=await pending;
[answer,busy,v.get(),selected.get(v),selected.run(fn(){v.get()+1})]'''
        with observe_resource_frames() as seen:self.assertEqual(self.run_source(source)[0],[42,'context_busy',0,42,43])
        self.assertIn('ContextRunFrame',seen['frames'])
