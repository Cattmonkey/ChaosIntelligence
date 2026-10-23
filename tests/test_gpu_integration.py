"""GPU numerical Tasks share ordinary language, agents and public interfaces."""
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

BODY='''async fn calculate(values:List)->Record {
 await gpu_map(fn(x:Number)->Number{x*x+2},values,device="cpu")
}'''
SOURCE=BODY+'let answer=await calculate([1,2,3]);[answer.values,answer.device,answer.dtype]'
EXPECTED=[[3.0,6.0,11.0],'cpu','float32']


class GPUIntegration(unittest.TestCase):
    def test_program_and_formatter_keep_same_numerical_task(self):
        ir=parse_program(SOURCE)
        for source in (SOURCE,format_program(ir),Framework.format_source(SOURCE,line_width=42)):
            self.assertEqual(Framework.evaluate(source),EXPECTED)
            self.assertEqual(parse_program(source)['semanticHash'],ir['semanticHash'])

    def test_import_and_hygienic_function_parameter(self):
        files={'kernel.chaos':'macro create(){quote{fn(value:Number)->Number{value*2+1}}};export{create}'}
        source='import "kernel.chaos" as K;let value=99;let r=await gpu_map(K.create!(),[1,2,3],device="cpu");[r.values,value]'
        self.assertEqual(Framework.evaluate(source,files=files),[[3.0,5.0,7.0],99])

    def test_real_provider_calls_gpu_async_language_tool(self):
        registry=Registry()
        def choose(request,context):
            context.checkpoint()
            return ToolCall('calculate',request.input) if request.step==1 else Final(request.history[-1]['output'])
        registry.register_provider(ProviderSpec('choose',choose))
        source=BODY+'''agent worker{provider:"choose",tools:[]};
let bound=agent_bind(worker,[agent_tool("calculate",calculate,input_type="List",output_type="Record")]);
let r=await bound([1,2,3]);[r.values,r.device,r.dtype]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('gpu-tool',source=source,capabilities=[])
            self.assertEqual(app.start('gpu-tool',1,None,mode='source').result(10),EXPECTED)

    def test_real_team_function_role_can_await_gpu_task(self):
        definition={'kind':'team','name':'numeric','entry':'worker','roles':[
            {'id':'worker','parent':None,'capabilities':[],
             'agent':{'kind':'agent','name':'worker','config':{'provider':'local','tools':[]}}}]}
        source=BODY+'let t=team_from('+json.dumps(definition)+');let bound=team_bind(t,{worker:calculate});let r=await bound([1,2,3]);[r.values,r.device,r.dtype]'
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('gpu-team',source=source,capabilities=[])
            self.assertEqual(app.start('gpu-team',1,None,mode='source').result(10),EXPECTED)

    def test_system_agent_reads_current_gpu_documentation_and_checks_draft(self):
        import test_ide_agent as support
        fixture=support.IDEAgentTests();fixture.setUp()
        try:
            def write_from_reference(request):
                evidence=json.loads([m for m in request['messages'] if m.get('role')=='tool'][-1]['content'])
                self.assertEqual(evidence['topic'],'gpu-compute')
                self.assertIn('Float32',evidence['markdown'])
                return [('draft.write',{'path':'main.chaos','source':SOURCE})]
            result=fixture.wait(fixture.start([[('language.reference',{'topic':'gpu-compute'})],
                write_from_reference,[('draft.validate',{}),('draft.evaluate',{})], 'GPU candidate checked.']))
            self.assertEqual(result['status'],'completed',result.get('error'))
            self.assertTrue(result['validation']['ok'])
            self.assertEqual(result['checks'][0]['output'],EXPECTED)
            self.assertEqual(fixture.app.get_project('project')['source'],'input')
        finally:fixture.tearDown()

    def test_http_sdk_and_program_use_identical_source(self):
        with tempfile.TemporaryDirectory() as folder,Framework(settings=Settings(':memory:')) as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_address[1]))
                client.create_project('gpu',source=SOURCE,capabilities=[])
                self.assertEqual(client.start('gpu',1,None,mode='source').result(10),EXPECTED)
                self.assertEqual(Framework.evaluate(SOURCE),EXPECTED)
                self.assertEqual(client.program('gpu')['program']['semanticHash'],parse_program(SOURCE)['semanticHash'])
                self.assertEqual(client.compile(SOURCE)['artifactHash'],Framework.compile(SOURCE)['artifactHash'])
                with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_address[1])+'/api/v1/docs/gpu-compute') as response:
                    page=json.load(response)
                self.assertEqual(page['markdown'],__import__('chaos.documentation',fromlist=['Documentation']).Documentation().page('gpu-compute')['markdown'])
            finally:
                server.shutdown();thread.join(5);server.server_close()

    def test_cli_runs_without_interface_or_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.chaos';path.write_text(SOURCE)
            result=subprocess.run([sys.executable,'-m','chaos','run',str(path)],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),EXPECTED)

    def test_gpu_and_integer_function_precision_are_distinct(self):
        source='''let integer=compile_function(fn(x:Int)->Int{x*2});
let r=await gpu_map(fn(x:Number)->Number{x+1},[16777216],device="cpu");
[integer(100000000000000000000),r.values]'''
        self.assertEqual(Framework.evaluate(source),[200000000000000000000,[16777216.0]])

    @unittest.skipUnless(importlib.util.find_spec('torch') is not None,'optional GPU hardware backend absent')
    def test_actual_mps_kernel_runs_in_normal_platform_context(self):
        import torch
        if not torch.backends.mps.is_available():self.skipTest('MPS hardware unavailable')
        source='''async fn compute(values:List)->Record{
await gpu_map(fn(x:Number)->Number{x*x+2},values,device="mps")};
let r=await compute([1,2,3]);let m=await gpu_matmul([[1,2],[3,4]],[[1,2],[3,4]],device="mps");
[r,m]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('mps',source=source,capabilities=[])
            result=app.start('mps',1,None,mode='source').result(15)
        self.assertEqual(result[0]['values'],[3.0,6.0,11.0])
        self.assertEqual(result[1]['values'],[[7.0,10.0],[15.0,22.0]])
        self.assertTrue(all(r['device']=='mps:0' and r['backend']=='torch' and r['dtype']=='float32' for r in result))


if __name__=='__main__':unittest.main()
