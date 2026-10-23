"""One augmented syntax through source, inert tools, IR, CLI, HTTP and LSP."""
import copy,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.language import Evaluator,LanguageError,Parser
from chaos.ide_language import Analysis,analyze,format_source
from chaos.program import parse_program,decode_program,format_program,plain,patch_program,templates,children
from chaos.symbols import references,rename
from chaos.server import make_server

ROOT=Path(__file__).resolve().parent.parent
SOURCE='// 🙂 count is a name, not a string edit\nvar count:Int=5;count<<=3;count|=2;count'

class AugmentedSurfaces(unittest.TestCase):
    def kind(self,source,name='answer'):
        report=analyze(source);self.assertEqual(report['diagnostics'],[],report['diagnostics'])
        return next(row['type'] for row in report['symbols'] if row['name']==name)
    def test_ir_shape_and_all_operators_roundtrip(self):
        for op in ['+','-','*','/','%','**','&','|','^','<<','>>']:
            source='var count=2;count '+op+'= 1;count';program=parse_program(source);node=program['root']['statements'][1]
            self.assertEqual(set(node)-{'id','span'},{'kind','target','operator','value'});self.assertEqual(node['kind'],'aug_assign');self.assertEqual(node['operator'],op)
            self.assertEqual([slot for slot,_ in children(node)],['target','value'])
            emitted=format_program(program);self.assertEqual(plain(Parser(emitted).program()),plain(decode_program(program)))
            self.assertEqual(Evaluator().run(source),Evaluator().run(emitted))
        self.assertEqual(templates()['aug_assign']['kind'],'aug_assign')
    def test_invalid_ir_targets_operators_and_root_unquote_rejected(self):
        base=parse_program('var x=1;x+=2');node=base['root']['statements'][1]
        for target in ['literal','call','assign','slice','unquote']:
            candidate=copy.deepcopy(base);candidate['root']['statements'][1]['target']=copy.deepcopy(templates()[target])
            with self.subTest(target=target),self.assertRaises(LanguageError):decode_program(candidate)
        for op in ['//','&&','@','+=','+;tool("x",null)']:
            with self.subTest(op=op),self.assertRaises(LanguageError):patch_program(base,[{'op':'set','id':node['id'],'field':'operator','value':op}])
    def test_legacy_target_member_and_modern_item_keep_identity(self):
        source='class C{var x=1;fn __getitem__(self,k){40};fn __setitem__(self,k,v){self.x=v;null}}let c=C();c["x"]+=2;c.x'
        for version,expected,kind in [(1,3,'index'),(2,42,'subscript')]:
            p=parse_program(source,language_version=version);self.assertEqual(p['root']['statements'][2]['target']['kind'],kind)
            canonical=format_program(p);formatted=format_source(source,language_version=version)
            self.assertEqual(Evaluator().run(canonical),expected);self.assertEqual(Evaluator().run(formatted),expected)
            self.assertEqual(plain(Parser(formatted).program()),plain(decode_program(p)))
    def test_format_source_preserves_comments_spread_power_and_precedence(self):
        source='// *= <<= remain comments\nfn f(*xs,**kw){kw.n+xs[0]}var n=2;n**=3;let v=~+n;[f(*[n],**{n:2}),2**-2,v,(-2)**2,-2**2]'
        emitted=format_source(source);self.assertEqual(format_source(emitted),emitted);self.assertIn('// *= <<= remain comments',emitted)
        self.assertEqual(plain(Parser(source).program()),plain(Parser(emitted).program()));self.assertEqual(Evaluator().run(source),Evaluator().run(emitted))
    def test_ir_patch_reuses_target_and_changes_only_operation(self):
        p=parse_program(SOURCE);aug=p['root']['statements'][1];target=copy.deepcopy(aug['target'])
        changed=patch_program(p,[{'op':'set','id':aug['id'],'field':'operator','value':'**'}]);self.assertEqual({k:v for k,v in changed['root']['statements'][1]['target'].items() if k!='span'},{k:v for k,v in target.items() if k!='span'})
        self.assertEqual(Evaluator().run(format_program(changed)),127);self.assertEqual(Evaluator().run(SOURCE),42)
    def test_integer_and_protocol_analysis_without_evaluation(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            for expression in ['1|2','1&2','1^2','1<<2','8>>2','~2','+2','2**3']:
                self.assertEqual(self.kind('let answer='+expression),'Int')
            self.assertEqual(self.kind('let answer=2**-2'),'Number')
            self.assertEqual(self.kind('let answer=2**-0'),'Int')
            self.assertEqual(analyze('var x:Int=2;var y:Int=3;x**=y')['diagnostics'],[])
            self.assertEqual(self.kind('class C{fn __and__(self,v)->String{"x"}}let answer=C()&1'),'String')
            self.assertEqual(self.kind('class C{fn __pos__(self)->Int{42}}let answer=+C()'),'Int')
            self.assertEqual(self.kind('class C{fn __invert__(self)->Int{42}}let answer=~C()'),'Int')
            self.assertEqual(self.kind('class C{fn __rshift__(self,v)->Int{42}}let answer=1>>C()'),'Any')
    def test_augmented_contracts_are_checked_and_hooks_remain_unknown_when_mutable(self):
        for source in ['let x=1;x+=2','class C{let x=1}let c=C();c.x+=2','var x:Int=1;x/=2']:
            self.assertTrue(analyze(source)['diagnostics'],source)
        self.assertEqual(self.kind('var x:Int=1;let answer=do{x+=2}'),'Int')
        self.assertEqual(self.kind('class C{fn __iadd__(self,v)->Int{42}}var c=C();let answer=do{c+=1}'),'Any')
        self.assertEqual(self.kind('class C{fn __iadd__(self,v)->Int{42}}class Box{var value=C()}let b=Box();let answer=do{b.value+=1}'),'Any')
    def test_analysis_does_not_rewalk_captured_receiver_key_or_rhs(self):
        original=Analysis.walk;seen={}
        def measured(this,node,env):seen[id(node)]=seen.get(id(node),0)+1;return original(this,node,env)
        with patch.object(Analysis,'walk',measured):analyze('input[fn(){1}()]+=fn(){2}()')
        self.assertTrue(seen);self.assertEqual(max(seen.values()),1)
    def test_symbols_augmented_write_is_single_unicode_edit(self):
        source='// 🙂 变量\nvar 变量=5;变量<<=3;变量|=2;变量';position=len(source[:source.index('var ')+4].encode('utf-16-le'))//2
        refs=references(source,position=position);self.assertTrue(refs['complete']);self.assertEqual([row['kind'] for row in refs['references']],['declaration','write','write','read'])
        changed=rename(source,position=position,new_name='答案')['files']['main.chaos'];self.assertEqual(changed,'// 🙂 变量\nvar 答案=5;答案<<=3;答案|=2;答案');self.assertEqual(Evaluator().run(changed),42)
    def test_augmented_computed_target_references_and_rename_are_exact(self):
        source='var index=0;let record=input;record[index]+=index;index';report=references(source,position=source.index('index'))
        self.assertEqual(len(report['references']),4);self.assertTrue(report['complete']);changed=rename(source,position=source.index('index'),new_name='key')['files']['main.chaos'];self.assertIn('record[key]+=key',changed)
    def test_http_sdk_ir_edit_cas_and_graph_run_share_language(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('augmented',source=SOURCE,capabilities=[])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='source').result(),42)
                program=client.program(p['id'])['program'];node=program['root']['statements'][1]
                changed=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':node['id'],'field':'operator','value':'**'}]);self.assertEqual(client.start(p['id'],changed['version'],None,mode='graph').result(),127)
                source=changed['source'];position=source.index('count');refs=client.references(source,position=position);self.assertEqual(len(refs['references']),4)
                self.assertEqual(client.format_source(source),Framework.format_source(source));self.assertEqual(client.analyze(source)['diagnostics'],[])
            finally:server.shutdown();thread.join(3);server.server_close()
    def test_cli_check_fmt_ir_run(self):
        with tempfile.TemporaryDirectory()as folder:
            source=Path(folder)/'main.chaos';source.write_text(SOURCE);env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ['check','fmt','ir','run']:
                result=subprocess.run([sys.executable,'-m','chaos',command,str(source)],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)
    def test_real_lsp_augmented_rename_and_tokens(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);path=root/'main.chaos';path.write_text(SOURCE);client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(len(SOURCE[:SOURCE.index('var count')+4].encode('utf-16-le'))//2)}
                renamed=client.request('textDocument/rename',{**params,'newName':'result'});self.assertEqual(len(renamed['documentChanges'][0]['edits']),4)
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),4)
                tokens=client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}});self.assertTrue(tokens['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
