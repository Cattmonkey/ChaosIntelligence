"""Structured native regions use the same language and agent execution path."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from chaos import Client, Evaluator, Final, Framework, ProviderSpec, Registry, Settings, ToolCall
from chaos.program import parse_program, format_program
from chaos.server import make_server

NATIVE = importlib.util.find_spec('llvmlite') is not None
BODY = '''fn magnitude(x:Int,/,*,by:Int=2)->Int{
  let doubled=x*by;
  if doubled>=0{
    let answer=doubled+2;
    if answer>10{answer}else{answer*2}
  }else{return -doubled+2}
}'''
SOURCE = BODY + '''
let f=compile_function(magnitude);
let answers=[f(20),f(-20),f(3)];[answers,compile_info(f)]'''


class StructuredJITPortableIntegration(unittest.TestCase):
    def test_source_ir_format_and_sdk_share_result_and_semantics(self):
        ir=parse_program(SOURCE)
        for source in (SOURCE,format_program(ir),Framework.format_source(SOURCE,line_width=42)):
            answer,info=Framework.evaluate(source)
            self.assertEqual(answer,[42,42,16])
            self.assertEqual(info['status'],'native' if NATIVE else 'interpreter')
            self.assertEqual(parse_program(source)['semanticHash'],ir['semanticHash'])

    def test_hygienic_local_binding_and_imported_function(self):
        files={'forms.chaos':'''macro maker(){quote{fn(value:Int)->Int{
let local=value*2;if local>=0{local+2}else{-local+2}}}};export{maker}'''}
        source='''import "forms.chaos" as F;let local=99;
let f=compile_function(F.maker!());[f(20),f(-20),local]'''
        self.assertEqual(Framework.evaluate(source,files=files),[42,42,99])

    def test_system_agent_generates_structured_function_candidate(self):
        import test_ide_agent as support
        fixture=support.IDEAgentTests();fixture.setUp()
        try:
            def write_from_reference(request):
                messages=[m for m in request['messages'] if m.get('role')=='tool']
                evidence=json.loads(messages[-1]['content'])
                self.assertEqual(evidence['topic'],'native-functions')
                self.assertIn('局部',evidence['markdown'])
                return [('draft.write',{'path':'main.chaos','source':SOURCE})]
            result=fixture.wait(fixture.start([[('language.reference',{'topic':'native-functions'})],
                write_from_reference,[('draft.validate',{}),('draft.evaluate',{})],
                'Structured candidate validated.']))
            self.assertEqual(result['status'],'completed',result.get('error'))
            self.assertTrue(result['validation']['ok'])
            self.assertEqual(result['checks'][0]['output'][0],[42,42,16])
            self.assertEqual(fixture.app.get_project('project')['source'],'input')
        finally:fixture.tearDown()


@unittest.skipUnless(NATIVE,'optional llvmlite backend unavailable')
class StructuredJITNativeIntegration(unittest.TestCase):
    def test_normal_platform_context_and_mixed_arbitrary_precision(self):
        source='''let f=compile_function(fn(x:Int)->Int{let square=x*x;square*square},"native");
let answer=f(60000);[answer,compile_info(f)]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('mixed',source=source,capabilities=[])
            answer,info=app.start('mixed',1,None,mode='source').result(10)
        self.assertEqual(answer,12960000000000000000)
        self.assertEqual(info['nativeCalls'],1)
        self.assertEqual(info['interpreterCalls'],1)
        self.assertEqual(info['lastFallbackReason'],'argument_guard')

    def test_real_agent_provider_calls_structured_language_tool(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('magnitude',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        source='''let f=compile_function(fn(x:Int)->Int{let y=x*2;if y>=0{y+2}else{-y+2}},"native");
agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("magnitude",f,input_type="Int",output_type="Int")]);
[await bound(-20),compile_info(f)]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('tool',source=source,capabilities=[])
            answer,info=app.start('tool',1,None,mode='source').result(10)
        self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],1)

    def test_structured_compiled_function_is_real_team_role(self):
        definition={'kind':'team','name':'native','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':[],
             'agent':{'kind':'agent','name':'worker','config':{'provider':'local','tools':[]}}}]}
        source=BODY+'let f=compile_function(magnitude,"native");let t=team_from('+json.dumps(definition)+');let bound=team_bind(t,{worker:f});[await bound(-20),compile_info(f)]'
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('team',source=source,capabilities=[])
            answer,info=app.start('team',1,None,mode='source').result(10)
        self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],1)

    def test_trace_uses_original_control_and_expression_events(self):
        events=[]
        with Framework(settings=Settings(':memory:'),trace_handler=events.append) as app:
            app.create_project('trace',source=SOURCE,capabilities=[])
            answer,info=app.start('trace',1,None,mode='source',trace=True).result(10)
        self.assertEqual(answer,[42,42,16]);self.assertEqual(info['nativeCalls'],0)
        self.assertEqual(info['interpreterCalls'],3)
        self.assertTrue(any(e.get('event')=='expression' and e.get('kind')=='if' for e in events))

    def test_debugger_sees_real_local_and_selected_nested_branch(self):
        source=BODY+'\nlet f=compile_function(magnitude,"native");let result=f(20);[result,compile_info(f)]'
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('debug',source=source,capabilities=[])
            run=app.start_debug('debug',1,pause_on_entry=False,breakpoints=[{'file':'main.chaos','line':5}])
            deadline=time.monotonic()+8
            while True:
                state=run.debugger()
                if state['status']=='paused':break
                self.assertEqual(state['status'],'running',str(run.get()))
                self.assertLess(time.monotonic(),deadline);time.sleep(.005)
            values={v['name']:v['value'] for frame in state['frames'] for v in frame['locals']}
            self.assertEqual(values['doubled'],40);self.assertEqual(values['answer'],42)
            run.debug_command('breakpoints',breakpoints=[])
            run.debug_command('continue',pause_id=state['pauseId'])
            answer,info=run.result(8)
        self.assertEqual(answer,42);self.assertEqual(info['nativeCalls'],0)

    def test_http_and_sdk_use_same_structured_source_snapshot(self):
        with tempfile.TemporaryDirectory() as folder,Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                client.create_project('remote',source=SOURCE,capabilities=[])
                answer=client.start('remote',1,None,mode='source',trace=False).result(10)
                self.assertEqual(answer,Framework.evaluate(SOURCE))
                self.assertEqual(answer[1]['nativeCalls'],3)
                self.assertEqual(client.program('remote')['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
            finally:
                server.shutdown();thread.join(5);server.server_close()


if __name__=='__main__':unittest.main()
