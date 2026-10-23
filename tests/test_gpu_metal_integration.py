"""Compiled numerical Tasks remain language values across agents and interfaces."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from chaos import Client, Final, Framework, ProviderSpec, Registry, Settings, ToolCall
from chaos.program import parse_program, format_program
from chaos.server import make_server

BODY='''async fn calculate(values:List)->Record{
 let job=gpu_map(fn(x:Number)->Number{x*x+2},values,device="cpu",compiler="auto");
 let result=await job;{result:result,kernel:gpu_kernel_info(job)}
}'''
ENDING='let answer=await calculate([1,2,3]);[answer.result.values,answer.result.device,answer.kernel.compiler,answer.kernel.isolated]'
SOURCE=BODY+ENDING
EXPECTED=[[3.0,6.0,11.0],'cpu','tensor',False]

class MetalLanguageIntegration(unittest.TestCase):
    def test_program_and_formatter_preserve_compiler_evidence(self):
        program=parse_program(SOURCE)
        for source in (SOURCE,format_program(program),Framework.format_source(SOURCE,line_width=42)):
            self.assertEqual(Framework.evaluate(source),EXPECTED)
            self.assertEqual(parse_program(source)['semanticHash'],program['semanticHash'])

    def test_import_and_macro_hygiene_keep_ir_identity(self):
        files={'kernel.chaos':'macro create(){quote{fn(value:Number)->Number{value*2+1}}};export{create}'}
        source='import "kernel.chaos" as K;let value=99;let job=gpu_map(K.create!(),[1,2,3],device="cpu");let hash=gpu_kernel_info(job).irHash;let r=await job;[r.values,value,hash==gpu_kernel_info(job).irHash]'
        self.assertEqual(Framework.evaluate(source,files=files),[[3.0,5.0,7.0],99,True])

    def test_provider_calls_async_tool_that_reports_compilation(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('calculate',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        source=BODY+'''agent worker{provider:"choose",tools:[]};let calculateAgent=agent_bind(worker,[agent_tool("calculate",calculate,input_type="List",output_type="Record")]);let answer=await calculateAgent([1,2,3]);[answer.result.values,answer.result.device,answer.kernel.compiler,answer.kernel.isolated]'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('compiled-tool',source=source,capabilities=[])
            self.assertEqual(app.start('compiled-tool',1,None,mode='source').result(15),EXPECTED)

    def test_team_function_role_keeps_same_compilation_contract(self):
        definition={'kind':'team','name':'numeric','entry':'worker','roles':[{'id':'worker','parent':None,'capabilities':[],'agent':{'kind':'agent','name':'worker','config':{'provider':'local','tools':[]}}}]}
        source=BODY+'let t=team_from('+json.dumps(definition)+');let bound=team_bind(t,{worker:calculate});let answer=await bound([1,2,3]);[answer.result.values,answer.result.device,answer.kernel.compiler,answer.kernel.isolated]'
        with Framework(settings=Settings(':memory:'))as app:
            app.create_project('compiled-team',source=source,capabilities=[])
            self.assertEqual(app.start('compiled-team',1,None,mode='source').result(15),EXPECTED)

    def test_system_agent_uses_current_metal_guide_for_draft(self):
        import test_ide_agent as support
        fixture=support.IDEAgentTests();fixture.setUp()
        try:
            def from_guide(request):
                evidence=json.loads([m for m in request['messages'] if m.get('role')=='tool'][-1]['content'])
                self.assertEqual(evidence['topic'],'gpu-metal');self.assertIn('gpu_kernel_info',evidence['markdown'])
                return [('draft.write',{'path':'main.chaos','source':SOURCE})]
            result=fixture.wait(fixture.start([[('language.reference',{'topic':'gpu-metal'})],from_guide,[('draft.validate',{}),('draft.evaluate',{})],'Compiled Task candidate checked.']))
            self.assertEqual(result['status'],'completed',result.get('error'));self.assertTrue(result['validation']['ok'])
            self.assertEqual(result['checks'][0]['output'],EXPECTED);self.assertEqual(fixture.app.get_project('project')['source'],'input')
        finally:fixture.tearDown()

    def test_http_sdk_program_and_online_guide_match(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                origin='http://127.0.0.1:'+str(server.server_address[1]);client=Client(origin)
                client.create_project('compiled',source=SOURCE,capabilities=[])
                self.assertEqual(client.start('compiled',1,None,mode='source').result(15),EXPECTED)
                self.assertEqual(Framework.evaluate(SOURCE),EXPECTED)
                self.assertEqual(client.program('compiled')['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
                self.assertEqual(client.compile(SOURCE)['artifactHash'],Framework.compile(SOURCE)['artifactHash'])
                with urllib.request.urlopen(origin+'/api/v1/docs/gpu-metal')as response:page=json.load(response)
                from chaos.documentation import Documentation
                self.assertEqual(page['markdown'],Documentation().page('gpu-metal')['markdown'])
            finally:server.shutdown();thread.join(5);server.server_close()

    def test_cli_runs_portable_compilation_example(self):
        path=Path(__file__).resolve().parent.parent/'examples/gpu-metal.chaos'
        result=subprocess.run([sys.executable,'-m','chaos','run',str(path)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);value=json.loads(result.stdout)
        self.assertEqual(value['values'],[3.0,6.0,11.0]);self.assertEqual(value['actualDevice'],'cpu')
        self.assertEqual(value['kernel']['compiler'],'tensor');self.assertFalse(value['kernel']['isolated']);self.assertEqual(value['cold'],'created')

    @unittest.skipUnless(importlib.util.find_spec('torch') is not None,'optional GPU backend absent')
    def test_true_metal_kernel_in_agent_function_tool(self):
        import torch
        if not torch.backends.mps.is_available() or not callable(getattr(torch.mps,'compile_shader',None)):self.skipTest('MPS Metal compiler unavailable')
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('calculate',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        body=BODY.replace('device="cpu",compiler="auto"','device="mps",compiler="metal"')
        source=body+'''agent worker{provider:"choose",tools:[]};let bound=agent_bind(worker,[agent_tool("calculate",calculate,input_type="List",output_type="Record")]);await bound([1,2,3])'''
        with Framework(registry=registry,settings=Settings(':memory:'))as app:
            app.create_project('true-metal-tool',source=source,capabilities=[])
            value=app.start('true-metal-tool',1,None,mode='source').result(30)
        self.assertEqual(set(value['result']),{'values','device','backend','dtype','fallbackReason'})
        self.assertEqual(value['result']['values'],[3.0,6.0,11.0]);self.assertEqual(value['result']['device'],'mps:0')
        self.assertEqual(value['kernel']['compiler'],'metal');self.assertTrue(value['kernel']['isolated']);self.assertEqual(value['kernel']['dispatches'],1)
        self.assertEqual(len(value['kernel']['sourceHash']),64)

if __name__=='__main__':unittest.main()
