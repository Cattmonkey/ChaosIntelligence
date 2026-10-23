"""Additive artifact surfaces use the one validated loader and original runtime."""
import copy,json,os,socket,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Settings,Client,Evaluator,LanguageError,compile_artifact,load_artifact,compile_program,parse_program,format_program
from chaos.compiled_flow_state import FlowState
from chaos.client import ValidationError,APIError
from chaos.server import make_server
import _artifact_surfaces219 as fixture

ROOT=Path(__file__).resolve().parent.parent

class ArtifactSDKSurface219(unittest.TestCase):
    def test_public_sdk_framework_roundtrip_executes_actual_saved_templates(self):
        import chaos
        self.assertIn('compile_artifact',chaos.__all__);self.assertIn('load_artifact',chaos.__all__)
        artifact=compile_artifact(fixture.SOURCE);self.assertEqual(artifact,Framework.compile_artifact(fixture.SOURCE))
        encoded=json.dumps(artifact,sort_keys=True,ensure_ascii=True,allow_nan=False)
        data=json.loads(encoded);before=copy.deepcopy(data);bundle=load_artifact(data);other=Framework.load_artifact(data)
        self.assertIsNot(bundle,other);self.assertEqual(Framework.validate_artifact(data),bundle.summary());self.assertEqual(data,before)
        for loaded in (bundle,other):
            states=[];original=FlowState.__init__
            def entered(state,*args,**kwargs):original(state,*args,**kwargs);states.append(state)
            with patch.object(FlowState,'__init__',entered):result=Framework.evaluate(loaded,bindings={'input':fixture.INPUT})
            self.assertEqual(result,fixture.EXPECTED);self.assertTrue(states)
            for state in states:
                self.assertTrue(state.program._artifact_decoded);self.assertEqual(state.program._artifact_hash,data['artifactHash']);self.assertTrue(state.program._artifact_template_id);self.assertGreater(state.program.resume_calls,0)
                self.assertTrue(state.closed);self.assertEqual(state.frames,[]);self.assertEqual(state._owned_activations,[]);self.assertEqual(state._transfers,[]);self.assertIsNone(state.pending_cursor);self.assertIsNone(state.pending_request)
        self.assertEqual(data,before)

    def test_build_load_do_not_run_effects_or_compile_function_formation(self):
        source='var defaults=0;fn make(){defaults+=1;40};let f=fn(x=make()){x+2};[defaults,type_of(f)]'
        with patch.object(Evaluator,'run',side_effect=AssertionError('User execution during build/load')):
            artifact=compile_artifact(source);loaded=load_artifact(artifact)
            inert=compile_artifact('tool("unknown",{});throw "must not execute"');load_artifact(inert)
        with patch('chaos.compilation_artifact.link_flow_template',side_effect=AssertionError('Implicit function compilation')):
            self.assertEqual(Framework.evaluate(loaded),[1,'Function'])
            self.assertEqual(Framework.evaluate(load_artifact(artifact)),[1,'Function'])

    def test_original_compilation_report_and_source_IR_remain_separate(self):
        report=compile_program(fixture.SOURCE);self.assertEqual(report['schema'],'chaos.compilation/1');self.assertEqual(report['target'],'portable-ast')
        with self.assertRaises(LanguageError)as caught:load_artifact(report)
        self.assertEqual(caught.exception.code,'artifact_error')
        ir=parse_program(fixture.SOURCE);formatted=format_program(ir)
        self.assertEqual(parse_program(formatted)['semanticHash'],ir['semanticHash'])
        self.assertEqual(Framework.evaluate(fixture.SOURCE,bindings={'input':40}),fixture.EXPECTED)

    def test_artifact_cli_fresh_process_build_validate_run_without_checkout_cwd_or_network(self):
        with tempfile.TemporaryDirectory(prefix='artifact219-outside-')as folder:
            work=Path(folder);source=work/'main.chaos';source.write_text(fixture.SOURCE);artifact_file=work/'artifact.json'
            env={k:v for k,v in os.environ.items()if k in ('PATH','SYSTEMROOT','WINDIR','TMPDIR','TMP','TEMP','LANG','LC_ALL','HOME','USERPROFILE')}
            env.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',CHAOS_CONFIG=str(work/'private-settings.json'))
            def run(mode,*args,text=None,success=True):
                result=subprocess.run([sys.executable,'-I','-S','-B','-c',fixture.ISOLATED,str(ROOT),mode,*map(str,args)],cwd=work,env=env,input=text,text=True,capture_output=True,timeout=25)
                self.assertEqual(result.returncode,0 if success else 1,(args,result.stdout,result.stderr));return result
            built=run('cli','artifact','build',source,'--root',work);artifact_file.write_text(built.stdout)
            built_data=json.loads(built.stdout);self.assertEqual(built_data,compile_artifact(fixture.SOURCE))
            validated=json.loads(run('cli','artifact','validate',artifact_file).stdout);self.assertTrue(validated['valid'])
            source.unlink()
            self.assertEqual(json.loads(run('cli','artifact','run',artifact_file,'--input-json','40').stdout),fixture.EXPECTED)
            outside=json.loads(run('sdk',artifact_file).stdout);self.assertEqual(outside['result'],fixture.EXPECTED);self.assertTrue(outside['allDecoded']and outside['allClosed']);self.assertGreater(outside['actualFlowStates'],0)
            self.assertEqual(json.loads(run('cli','artifact','validate','-',text=built.stdout).stdout),validated)
            for bad in ('{"schema":0,"schema":1}', 'NaN', '[]'):
                result=run('cli','artifact','validate','-',text=bad,success=False);self.assertIn('error',json.loads(result.stderr))
            self.assertEqual(artifact_file.read_text(),built.stdout)

class ArtifactHTTPSurface219(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);work=Path(self.temp.name)
        settings=Settings(':memory:');settings.update({'providers':{name:{'baseURL':'http://127.0.0.1:1','clearKey':True}for name in settings.public()['providers']},'runtime':{'continuousEnabled':False,'codeEnabled':False,'browserEnabled':False,'workspace':str(work/'tasks')}},version=settings.public()['version'])
        self.app=Framework(':memory:',settings=settings);self.addCleanup(self.app.close)
        self.denied=[]
        self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=work/'control',update_home=work/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server)
        self.client=Client('http://127.0.0.1:'+str(self.server.server_port))
        original=socket.socket.connect;original_ex=socket.socket.connect_ex;port=self.server.server_port
        def admit(address):
            if not(type(address)is tuple and address[0]=='127.0.0.1'and address[1]==port):self.denied.append(repr(address));raise AssertionError('Unexpected network connection')
        def connect(sock,address):admit(address);return original(sock,address)
        def connect_ex(sock,address):admit(address);return original_ex(sock,address)
        self.net=patch.object(socket.socket,'connect',connect);self.net_ex=patch.object(socket.socket,'connect_ex',connect_ex);self.net.start();self.net_ex.start();self.addCleanup(self.net.stop);self.addCleanup(self.net_ex.stop)
    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3);self.assertFalse(self.thread.is_alive());self.assertEqual(self.denied,[])

    def test_client_build_validate_and_source_mirror_share_one_readonly_artifact(self):
        before=self.app.get_project('workspace');events=self.app.events('workspace')['lastEventId']
        direct=compile_artifact(fixture.SOURCE);remote=self.client.compile_artifact(fixture.SOURCE)
        self.assertEqual(remote,direct);self.assertEqual(self.client.validate_artifact(remote),load_artifact(direct).summary())
        legacy=self.client._request('POST','/language/compile-artifact',{'source':fixture.SOURCE,'files':{'main.chaos':'0'}})
        self.assertEqual(legacy,direct)
        self.assertEqual(self.app.get_project('workspace'),before);self.assertEqual(self.app.events('workspace')['lastEventId'],events)

    def test_readonly_build_and_validate_never_execute_effects_or_grant_permissions(self):
        before=self.app.get_project('workspace');source='tool("unknown",{});agent worker{provider:"absent",tools:[]};worker(42);throw "not executed"'
        with patch.object(Evaluator,'run',side_effect=AssertionError('Runtime execution through readonly API')):
            artifact=self.client.compile_artifact(source);summary=self.client.validate_artifact(artifact)
        self.assertTrue(summary['valid']);self.assertEqual(self.app.get_project('workspace'),before)
        with self.assertRaises(APIError):self.client._request('POST','/language/run-artifact',{'artifact':artifact})

    def test_HTTP_schema_method_tampering_and_source_authority_are_strict(self):
        for extra in ('target','capabilities','passed','file'):
            with self.subTest(extra=extra),self.assertRaises(ValidationError):self.client._request('POST','/language/compile-artifact',{'source':'42',extra:None})
        data=self.client.compile_artifact('42')
        for bad in (None,[],{},dict(data,unexpected=True),dict(data,artifactHash='0'*64)):
            with self.subTest(type=type(bad).__name__),self.assertRaises(ValidationError):self.client.validate_artifact(bad)
        with self.assertRaises(ValidationError):self.client._request('POST','/language/validate-artifact',{'artifact':data,'execute':True})
        with self.assertRaises(APIError)as method:self.client._request('GET','/language/compile-artifact')
        self.assertEqual(method.exception.status,405)
        with self.assertRaises(ValidationError):self.client.compile_artifact('42',files={'bad.chaos':'fn ('})
        with self.assertRaises(ValidationError):self.client.compile_artifact('42',language_versions={'main.chaos':True})

    def test_openapi_documents_artifact_and_original_report_without_execution_route(self):
        spec=self.client._request('GET','/openapi.json');schemas=spec['components']['schemas']
        self.assertEqual(spec['paths']['/language/compile']['post']['operationId'],'compileSource')
        self.assertEqual(spec['paths']['/language/compile-artifact']['post']['operationId'],'compilePortableArtifact')
        self.assertEqual(spec['paths']['/language/validate-artifact']['post']['operationId'],'validatePortableArtifact')
        self.assertNotIn('/language/run-artifact',spec['paths'])
        self.assertFalse(schemas['CompiledArtifact']['additionalProperties']);self.assertIn('fileOrder',schemas['CompiledArtifact']['required'])
        self.assertEqual(set(schemas['ArtifactValidationRequest']['properties']),{'artifact'})
        self.assertEqual(set(schemas['CompiledArtifactRequest']['properties']),{'source','files','entry','languageVersions'})
        artifact=self.client.compile_artifact('42');self.assertTrue(set(schemas['CompiledArtifact']['required'])<=set(artifact))
