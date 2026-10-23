"""Same canonical UI artifact through native, SDK, HTTP and CLI."""
from pathlib import Path
import json,os,subprocess,sys,tempfile,threading,unittest
from chaos import Client,Framework,Settings
from chaos.client import ValidationError
from chaos.server import make_server
SOURCE='root = Text("Hello")'
class UIAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.app=Framework(settings=Settings(':memory:'));self.addCleanup(self.app.close)
        self.server=make_server(port=0,framework=self.app,state_dir=Path(self.temp.name)/'control',update_home=Path(self.temp.name)/'updates');self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close_server);self.client=Client('http://127.0.0.1:'+str(self.server.server_port))
    def close_server(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
    def test_native_sdk_http_schema_hash_and_requirements_agree(self):
        d=self.client.ui_compile(SOURCE,title='Demo');self.assertEqual(d,self.app.ui_compile(SOURCE,title='Demo'));self.assertEqual(d,Framework.evaluate('ui_definition(ui_from_openui(input,title="Demo"))',{'input':SOURCE}));self.assertEqual(self.client.ui_schema(),self.app.ui_schema())
        requirements=[{'component':'Text','minimum':1},{'path':'/props/text','equals':'Hello'}];self.assertEqual(self.client.ui_validate(d,requirements=requirements),self.app.ui_validate(d,requirements=requirements));self.assertTrue(self.client.ui_validate(d,requirements=requirements)['passed']);self.assertFalse(self.client.ui_validate(d,requirements=[{'component':'Table','minimum':1}])['passed'])
        for fmt in ('html','json','chaos','openui'):self.assertEqual(self.client.ui_export(d,format=fmt),self.app.ui_export(d,format=fmt))
    def test_invalid_profile_closed_fields_and_tampered_hash_fail(self):
        for source in ('root = Query("url")','root = Text(1 + 2)','root = Text("x")\nroot = Text("y")','root = Text("x"'):
            with self.subTest(source=source),self.assertRaises(ValidationError):self.client.ui_compile(source)
        with self.assertRaises(ValidationError):self.client._request('POST','/ui/compile',{'source':SOURCE,'apiKey':'bad'})
        d=self.client.ui_compile(SOURCE);d['title']='changed'
        with self.assertRaises(ValidationError):self.client.ui_export(d)
    def test_openapi_additive_ui_contract(self):
        spec=self.client._request('GET','/openapi.json')
        for path,method,name in (('/ui/schema','get','uiComponentSchema'),('/ui/compile','post','compileUI'),('/ui/validate','post','validateUI'),('/ui/export','post','exportUI')):self.assertEqual(spec['paths'][path][method]['operationId'],name)
        self.assertFalse(spec['components']['schemas']['UICompile']['additionalProperties']);self.assertEqual(spec['paths']['/agents/schema']['get']['operationId'],'agentConfigurationSchema')
    def test_cli_compile_export_validate_and_output_refuses_input(self):
        p=Path(self.temp.name)/'view.openui';p.write_text(SOURCE);env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
        def run(*args):return subprocess.run([sys.executable,'-m','chaos','ui',*args],cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True)
        r=run('compile',str(p),'--title','Demo');self.assertEqual(r.returncode,0,r.stderr);d=json.loads(r.stdout);self.assertEqual(d,self.client.ui_compile(SOURCE,title='Demo'))
        r=run('export',str(p),'--format','html');self.assertEqual(r.returncode,0,r.stderr);self.assertIn('Content-Security-Policy',r.stdout)
        req=Path(self.temp.name)/'req.json';req.write_text('[{"component":"Table","minimum":1}]');r=run('validate',str(p),'--requirements',str(req));self.assertEqual(r.returncode,1);self.assertFalse(json.loads(r.stdout)['passed'])
        self.assertNotEqual(run('export',str(p),'--output',str(p)).returncode,0);self.assertEqual(p.read_text(),SOURCE)
