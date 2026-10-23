"""Native kernels remain ordinary language functions through real agent/runtime paths."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chaos import Client, Evaluator, Final, Framework, ProviderSpec, Registry, Settings, ToolCall
from chaos.program import format_program, parse_program
from chaos.server import make_server

HAS_LLVM = importlib.util.find_spec('llvmlite') is not None
PURE = '''fn score(x:Int,y:Int=2)->Int{x*y+2}
let fast=compile_function(score,target="native");
let answer=fast(20);let large=fast(100000000000000000000);
[answer,large,compile_info(fast)]'''


def team_definition():
    return {'kind':'team','name':'native','entry':'worker','roles':[
        {'id':name,'parent':parent,'capabilities':[],
         'agent':{'kind':'agent','name':name,'config':{'provider':'local','tools':[]}}}
        for name,parent in [('lead',None),('worker','lead')]]}


class JITPortableIntegration(unittest.TestCase):
    def test_compiled_fallback_function_cannot_escape_team_authority(self):
        calls=[]
        source='''let t=team_from(input);fn wide(x){tool("secret",x)};
let bound=team_bind(t,{worker:fn(x){compile_function(wide)}});
let escaped=await bound(0);
[try{escaped(42)}catch e{e.code},wide(43)]'''
        self.assertEqual(Evaluator(tool_handler=lambda n,v:calls.append(v) or v).run(
            source,{'input':team_definition()}),['capability_error',43])
        self.assertEqual(calls,[43])

    def test_system_agent_reads_native_contract_and_evaluates_program_proposal(self):
        import test_ide_agent as support
        fixture=support.IDEAgentTests();fixture.setUp()
        try:
            def use_reference(request):
                messages=[m for m in request['messages'] if m.get('role')=='tool']
                evidence=json.loads(messages[-1]['content'])
                self.assertEqual(evidence['topic'],'native-functions')
                self.assertIn('compile_function',evidence['markdown'])
                self.assertIn('检查点',evidence['markdown'])
                return [('draft.write',{'path':'main.chaos','source':'let f=compile_function(fn(x:Int){x*2});[f(21),compile_info(f).requestedTarget]'})]
            result=fixture.wait(fixture.start([[('language.reference',{'topic':'native-functions'})],
                use_reference,[('draft.validate',{}),('draft.evaluate',{})],'Native function proposal validated.']))
            self.assertEqual(result['status'],'completed',result.get('error'))
            self.assertEqual(result['checks'][0]['output'],[42,'auto'])
            self.assertTrue(result['validation']['ok'])
            self.assertEqual(fixture.app.get_project('project')['source'],'input')
        finally:fixture.tearDown()

    def test_auto_roundtrip_keeps_function_behavior_and_large_integers(self):
        source=PURE.replace('target="native"','target="auto"')
        ir=parse_program(source)
        for text in (source,format_program(ir),Framework.format_source(source,line_width=45)):
            answer,large,info=Framework.evaluate(text)
            self.assertEqual([answer,large],[42,200000000000000000002])
            self.assertEqual(info['requestedTarget'],'auto')
            self.assertIn(info['status'],('native','interpreter','unsupported'))
            self.assertEqual(parse_program(text)['semanticHash'],ir['semanticHash'])


@unittest.skipUnless(HAS_LLVM,'optional llvmlite native extra unavailable')
class JITNativeIntegration(unittest.TestCase):
    def test_normal_context_reaches_native_despite_lifecycle_trace_handler(self):
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('native',source=PURE,capabilities=[])
            run=app.start('native',1,None,mode='source')
            answer,large,info=run.result(10)
            self.assertEqual([answer,large],[42,200000000000000000002])
            self.assertEqual(info['status'],'native')
            self.assertEqual(info['nativeCalls'],1)
            self.assertEqual(info['interpreterCalls'],1)
            self.assertEqual(info['backend'],'llvm')
            self.assertTrue(info['target'])

    def test_real_provider_invokes_native_language_tool(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('twice',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        source='''let fast=compile_function(fn(x:Int)->Int{x*2},target="native");
agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("twice",fast,input_type="Int",output_type="Int")]);
[await bound(21),compile_info(fast)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('tool',source=source,capabilities=[])
            answer,info=app.start('tool',1,None,mode='source').result(10)
        self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],1)

    def test_native_function_is_real_team_role(self):
        source='let t=team_from('+json.dumps(team_definition())+''');
let f=compile_function(fn(x:Int,/,*,offset:Int=2)->Int{x*2+offset},target="native");
let bound=team_bind(t,{worker:f});[await bound(20),compile_info(f)]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('team',source=source,capabilities=[])
            run=app.start('team',1,None,mode='source')
            answer,info=run.result(10)
            self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],1)
            self.assertTrue(any('team.role' in event['type'] for event in app.events('team')['items']))

    def test_explicit_trace_preserves_expression_events_and_interpreted_body(self):
        events=[]
        with Framework(settings=Settings(':memory:'),trace_handler=events.append) as app:
            app.create_project('trace',source=PURE,capabilities=[])
            answer,large,info=app.start('trace',1,None,mode='source',trace=True).result(10)
        self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],0)
        self.assertEqual(info['interpreterCalls'],2)
        self.assertTrue(any(e.get('event')=='expression' and e.get('kind')=='binary' for e in events))

    def test_debugger_stops_inside_compiled_function_original_source(self):
        source='\n'.join(['fn score(x:Int)->Int{','  x*2','}',
            'let fast=compile_function(score,target="native");',
            'let answer=fast(21);[answer,compile_info(fast)]'])
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('debug',source=source,capabilities=[])
            run=app.start_debug('debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':2}])
            deadline=time.monotonic()+8
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',str(run.get()))
                self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            values={v['name']:v['value'] for frame in state['frames'] for v in frame['locals']}
            self.assertEqual(values['x'],21)
            run.debug_command('breakpoints',breakpoints=[])
            run.debug_command('continue',pause_id=state['pauseId'])
            answer,info=run.result(8)
        self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],0)
        self.assertEqual(info['interpreterCalls'],1)

    def test_hygienic_macro_parameters_and_module_function_ownership(self):
        files={'forms.chaos':'macro maker(){quote{fn(value:Int,/,*,offset:Int=2)->Int{value*2+offset}}};export{maker}'}
        source='''import "forms.chaos" as F;let value=99;
let fast=compile_function(F.maker!(),target="native");
[fast(20),fast(21,offset=0),value,compile_info(fast)]'''
        answer,other,value,info=Framework.evaluate(source,files=files)
        self.assertEqual([answer,other,value],[42,42,99]);self.assertEqual(info['nativeCalls'],2)

    def test_http_run_and_sdk_share_native_and_portable_program_snapshot(self):
        with tempfile.TemporaryDirectory() as folder,Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                client.create_project('http-native',source=PURE,capabilities=[])
                run=client.start('http-native',1,None,mode='source')
                result=run.result(10)
                self.assertEqual(result[:2],[42,200000000000000000002])
                self.assertEqual(result[2]['nativeCalls'],1)
                self.assertEqual(result,Framework.evaluate(PURE))
                self.assertEqual(client.program('http-native')['program']['semanticHash'],parse_program(PURE)['semanticHash'])
                self.assertEqual(client.compile(PURE)['target'],'portable-ast')
            finally:
                server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
