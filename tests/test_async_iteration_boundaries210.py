"""Owned anext cancellation, borrowed nested Task, and actual surface composition."""
import copy,json,os,socket,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Registry,ToolCall,Final,Client,Settings
from chaos import suspension
from chaos.language import Evaluator,Native
from chaos.server import make_server
from chaos.program import parse_program,format_program
from chaos.source_format import format_source
import test_module_frame_surfaces as surfaces
import test_generator_flow_surfaces208 as previous
import test_robot_values as robot_fixture
import _generator_flow208 as streams
from _effect_schedule158 import effect_schedule

ROOT=Path(__file__).resolve().parents[1]
SOURCE='''async fn* items(){yield 20;await sleep(0);yield 22};
async fn consume(){var out=[];async for x in items(){out=append(out,x)}else{out=append(out,42)};out};
let f=compile_function(consume,target="auto-flow");let cold=compile_flow_info(f).resumeCalls;
let value=await f();[value,cold,compile_flow_info(f).resumeCalls>0]'''
EXPECTED=[[20,22,42],0,True]

class AsyncIterationBoundaries210(unittest.TestCase):
    run_source=surfaces.ModuleFrameSurfaces.run_source

    def test_cancel_owns_anext_operation_and_preserves_nested_borrowed_task(self):
        rows=[]
        for target in streams.targets():
            effects=[];held=[];observed=[];base=[None]
            e=Evaluator(timeout=30,tool_handler=lambda n,v:v,effect_handler=lambda k,p,c:effects.append(p['name'])or p['value'])
            e.env.bind('choose',Native('choose',lambda f:streams.compile_choice(e,f,target),e,captures=()))
            def remember(child,consumer,stream):held.append((child,consumer,stream));base[0]=e.steps
            e.env.bind('remember',Native('remember',remember,e,captures=()))
            with streams.observe_states()as states,effect_schedule('first_wait')as schedule:
                wait=suspension.wait
                def waiting(futures,*a,**kw):
                    futures=tuple(futures)
                    try:
                        if held and not observed:
                            child,consumer,stream=held[0];future=child.runner.future
                            if future is not None and future in futures:
                                self.assertFalse(future.done());self.assertEqual(child.runner.state,'effect')
                                operation=consumer.runner.waiting;self.assertIs(type(operation),suspension.Task)
                                self.assertIsNot(operation,child);self.assertIs(operation.runner.waiting,child)
                                if target not in ('raw','interpreter'):
                                    live=[s for s in states if not s.closed];self.assertEqual(len(live),1);state=live[0]
                                    self.assertIs(state.pending_cursor,consumer.runner.iterator);self.assertIs(state.pending_request.task,operation)
                                else:self.assertEqual(states,[])
                                e.suspension.cancel(consumer);self.assertFalse(child.cancel_event.is_set());observed.append(operation)
                    finally:answer=wait(futures,*a,**kw)
                    return answer
                source='''let child=spawn((async fn(){tool("borrowed_loop_hold",42)})());async fn* source(work){yield await work;yield 99};
let stream=source(child);let f=choose(async fn(input){async for x in input{x}});let consumer=spawn(f(stream));remember(child,consumer,stream);await child'''
                with patch.object(suspension,'wait',waiting):value=e.run(source)
            child,consumer,stream=held[0];self.assertEqual(value,42);self.assertEqual(len(observed),1)
            self.assertEqual(child.state,'completed');self.assertFalse(child.cancel_event.is_set());self.assertEqual(consumer.state,'cancelled')
            self.assertEqual(observed[0].state,'cancelled');self.assertFalse(stream.busy)
            self.assertEqual(schedule['waitBoundaryReleases'],1);self.assertEqual(schedule['shutdownReleases'],0)
            self.assertTrue(all(s.closed and not s.frames and not s._owned_activations for s in states));self.assertEqual(e.frames,[]);self.assertEqual(e.depth,0)
            rows.append((value,e.steps-base[0],effects,child.state,consumer.state,observed[0].state,stream.state,dict(schedule)))
        self.assertTrue(all(row==rows[0]for row in rows),rows)

    def test_source_IR_Code_and_formatter_use_same_structural_consumer(self):
        for mode in ('source','graph'):
            with self.subTest(mode=mode),streams.observe_states()as states:value,_=self.run_source(SOURCE,mode=mode,capabilities=[])
            self.assertEqual(value,EXPECTED);self.assertTrue(states)
            self.assertTrue(any(row[0]=='enter'and row[1][0]=='async_for'for s in states for row in s.program.instructions))
        for text in (format_source(SOURCE),format_program(parse_program(SOURCE))):self.assertEqual(Framework.evaluate(text),EXPECTED)
        code='let f=compile_function(function_from(code_from("async fn(s){var out=[];async for x in s{out=append(out,x)};out}")),target="auto-flow");async fn* g(){yield 42};[await f(g()),compile_flow_info(f).resumeCalls>0]'
        self.assertEqual(Framework.evaluate(code),[[42],True])

    def test_agent_tool_team_bot_explicit_async_consumer_keeps_once_effects(self):
        registry=Registry();calls=[];effects=[]
        @registry.tool('fixture.gen208.audit')
        def audit(value,context):context.checkpoint();effects.append(value);return None
        @registry.provider('fixture.gen208.provider')
        def provider(request,context):context.checkpoint();calls.append(request.step);return ToolCall('work',request.input)if request.step==1 else Final(request.history[-1]['output'])
        caps=['fixture.gen208.audit'];team={'kind':'team','name':'async-loop','entry':'work','roles':[{'id':'work','parent':None,'capabilities':caps,'agent':{'kind':'agent','name':'fallback','config':{'provider':'local','tools':caps}}}]}
        source='''async fn* items(value){tool("fixture.gen208.audit",value);yield value;yield value+2};
async fn raw(value){var out=[];async for x in items(value){out=append(out,x)};out};let consume=compile_function(raw,target="auto-flow");
agent helper{provider:"fixture.gen208.provider",tools:["fixture.gen208.audit"]};let bound=agent_bind(helper,[agent_tool("work",consume,capabilities=["fixture.gen208.audit"])]);
let crew=team_bind(team_from(TEAM),{work:consume});async fn execute(plan,state){[await bound(40),await crew(40)]};fn verify(state){{done:true,context:state.context,output:state.evidence[0]}};
let b=bot("async loop",fn(state){state.input},execute,verify);[(await b(null)).output,compile_flow_info(consume).resumeCalls>0]'''.replace('TEAM',json.dumps(team))
        with streams.observe_states()as states:value,events=self.run_source(source,registry=registry,capabilities=caps)
        self.assertEqual(value,[[[40,42],[40,42]],True]);self.assertEqual(calls,[1,2]);self.assertEqual(effects,[40,40])
        self.assertTrue(states);self.assertTrue(all(s.closed and not s.generating for s in states))
        self.assertEqual(sum(row['type']=='agent.language_tool'for row in events),1);self.assertEqual(sum(row['type']=='team.role.started'for row in events),1)

    def test_robot_software_action_in_async_loop_is_admitted_once(self):
        source='''async fn* input(){yield 1;yield 2};async fn raw(){var out=[];async for x in input(){
async with await robot_open("rig") as r{let o=await robot_observe(r);let command=robot_command(o,{pan:0,tilt:0});let receipt=await r(command);
out=append(out,[receipt.status,receipt.targetVerified,command.used]);let duplicate=try{await r(command)}catch e{e.code};out=append(out,duplicate)};break};out};
let f=CHOICE;[await f(),compile_flow_info(f).resumeCalls>0]'''
        for compiled in (False,True):
            actual=source.replace('CHOICE','compile_function(raw,target="auto-flow")'if compiled else'raw');log=[]
            with robot_fixture.host(log)as app,streams.observe_states()as states:value=robot_fixture.run(app,actual)
            self.assertEqual(value,[[['sent',False,True],'robot_command_used'],compiled]);self.assertEqual([x[0]for x in log],['open','observe','act','close'])
            self.assertEqual(bool(states),compiled);self.assertTrue(all(s.closed for s in states))
            denied=[]
            with robot_fixture.host(denied)as app:self.assertEqual(robot_fixture.run(app,'try{'+actual+'}catch e{e.code}',caps=[]),'capability_error')
            self.assertEqual(denied,[])

    def test_outside_isolated_SDK_CLI_async_consumers(self):
        # Same established outside-process isolation as the delivered stream fixture.
        with patch.object(previous,'SOURCE',SOURCE),patch.object(previous,'EXPECTED',EXPECTED):
            previous.GeneratorFlowSurfaces208.test_outside_isolated_SDK_CLI_stream_consumers_use_no_model_or_hardware(self)

    def test_private_HTTP_runs_same_async_consumer_without_project_database(self):
        with tempfile.TemporaryDirectory(prefix='chaos-loop-http210-')as folder:
          settings=Settings(':memory:');public=settings.public()
          settings.update({'providers':{name:{'baseURL':'http://127.0.0.1:1','clearKey':True}for name in public['providers']},'runtime':{'continuousEnabled':False,'codeEnabled':False,'browserEnabled':False,'workspace':str(Path(folder)/'tasks')}},version=public['version'])
          with Framework(':memory:',settings=settings)as app,streams.observe_states()as states:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True)
            original_connect=socket.socket.connect;original_connect_ex=socket.socket.connect_ex;outside=[];allowed=server.server_address
            def connect(sock,address):
                if tuple(address)!=tuple(allowed):outside.append(address);raise AssertionError('non-private HTTP connection')
                return original_connect(sock,address)
            def connect_ex(sock,address):
                if tuple(address)!=tuple(allowed):outside.append(address);raise AssertionError('non-private HTTP connection')
                return original_connect_ex(sock,address)
            try:
                with patch.object(socket.socket,'connect',connect),patch.object(socket.socket,'connect_ex',connect_ex):
                    thread.start();client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                    project=client.create_project('async-loop210',source=SOURCE,capabilities=[])
                    handle=client.start(project['id'],project['version'],None,mode='source');self.assertEqual(handle.result(20),EXPECTED)
                    self.assertEqual(client.get_project(project['id'])['source'],SOURCE)
            finally:
                if thread.is_alive():server.shutdown()
                server.server_close();thread.join(timeout=5)
            self.assertFalse(thread.is_alive());self.assertEqual(outside,[]);self.assertTrue(states);self.assertTrue(all(s.closed for s in states))
