"""Structural state and resource patterns share inert authoring surfaces."""
import copy,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.language import Evaluator,LanguageError,Parser
from chaos.ide_language import Analysis,analyze
from chaos.program import SHAPES,children,decode_program,format_program,parse_program,patch_program,plain,templates
from chaos.source_format import format_source
from chaos.symbols import references,rename
from chaos.server import make_server

ROOT=Path(__file__).resolve().parent.parent
SOURCE='// 🙂 left remains in this comment\nvar left:Int=1;var right:Int=2;[left,right]=[20,22];import "std/context" as Context;@Context.contextmanager\nfn* pair(){yield ([left,right])};with pair() as [first,second]{first+second}'

class AssignmentSurfaces(unittest.TestCase):
    def test_three_shapes_roundtrip_and_whole_rhs_result(self):
        cases=[('var a=0;var b=0;var rest=[];[a,{x:b},..rest]=[20,{x:22},1,2];[a,b,rest]',[20,22,[1,2]]),
               ('var a=0;var rest={};let whole=do{{x:a,..rest}={x:42,y:1}};[a,rest,whole]',[42,{'y':1},{'x':42,'y':1}]),
               ('data Pair{Both(a,b)};var a=0;var b=0;Both(a,b)=Both(20,22);a+b',42),
               (SOURCE,42),
               ('import "std/context" as Context;@Context.asynccontextmanager\nasync fn* pair(){yield ({x:20,y:22})};async with pair() as {x:a,y:b}{a+b}',42),
               ('import "std/context" as Context;@Context.contextmanager\nfn* pair(x){yield (x)};with pair([20]) as [a],pair(a+22) as b{a+b}',62)]
        for source,expected in cases:
            with self.subTest(source=source):
                program=parse_program(source)
                for rendered in (format_program(program),format_source(source,line_width=40)):
                    self.assertEqual(plain(Parser(rendered).program()),plain(decode_program(program)))
                    self.assertEqual(Evaluator().run(rendered),expected)
        sample=parse_program(SOURCE)['root']['statements']
        self.assertEqual([field for field,_ in SHAPES['assign_pattern']],['pattern','value'])
        self.assertEqual([slot for slot,_ in children(sample[2])],['pattern','value'])
        for kind in ('assign_pattern','with_pattern','async_with_pattern'):
            self.assertEqual(templates()[kind]['kind'],kind)

    def test_invalid_ir_patterns_body_duplicate_and_rest_are_rejected(self):
        for source,index in [('var a=0;[a]=[1]',1),('with input as [a]{a}',0),('async with input as [a]{a}',0)]:
            base=parse_program(source)
            for kind in ('p_any','p_bind','p_literal','list','name','unquote'):
                candidate=copy.deepcopy(base);candidate['root']['statements'][index]['pattern']=copy.deepcopy(templates()[kind])
                with self.subTest(source=source,kind=kind),self.assertRaises(LanguageError):decode_program(candidate)
            for pattern in ('[a,a]','[a,..a]','{x:a,x:b}','[a,..[b]]'):
                with self.subTest(pattern=pattern),self.assertRaises(LanguageError):parse_program(pattern+'=[1,2]')
            if index==0:
                candidate=copy.deepcopy(base);candidate['root']['statements'][0]['body']=copy.deepcopy(templates()['literal'])
                with self.assertRaises(LanguageError):decode_program(candidate)

    def test_simple_names_and_bare_underscore_preserve_original_ir(self):
        for source,kind in [('var x=0;x=1','assign'),('var _=0;_=1','assign'),('with input as x{x}','with'),('with input as _{_}','with'),('async with input as _{_}','async_with')]:
            node=parse_program(source)['root']['statements'][-1]
            self.assertEqual(node['kind'],kind);self.assertNotIn('pattern',node)
        # Existing literal-only binding/match forms are still available.
        self.assertEqual(Evaluator().run('let 42=42;match 42{42=>true}'),True)

    def test_source_format_preserves_comments_spelling_versions_and_idempotence(self):
        source='// 🙂 keep {x:变量} = text\nvar 变量=0;var rest=[];[变量,..rest]=[0x,1]'
        source=source.replace('0x','42')
        formatted=format_source(source,line_width=40);self.assertIn('// 🙂 keep {x:变量} = text',formatted)
        self.assertEqual(format_source(formatted,line_width=40),formatted)
        self.assertEqual(parse_program(formatted)['semanticHash'],parse_program(source)['semanticHash'])
        source='class C{var x=0}let c=C();var value=0;[value]=[c["x"]];value'
        legacy=format_source(source,language_version=1);self.assertIn('.[',legacy)
        self.assertEqual(plain(Parser(legacy).program()),plain(decode_program(parse_program(source,language_version=1))))

    def test_assignment_analysis_checks_original_cells_without_new_declarations(self):
        source='var a:Int=1;var b:String="before";[a,b]=[20,"after"];[a,b]'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')):
            result=analyze(source,position=source.index('[a,b]')+2)
        self.assertEqual(result['diagnostics'],[]);self.assertEqual([(s['name'],s['type']) for s in result['symbols']],[('a','Int'),('b','String')])
        self.assertIn('Int',result['hover']['contents'])
        for source,code in [('let x=1;[x]=[2]','immutable_binding'),('[missing]=[2]','undefined_name'),('var x:Int=1;[x]=["bad"]','type_mismatch'),('var x=0;[x]=[1,2]','pattern_mismatch'),('var x=0;{a:x}={b:1}','pattern_mismatch')]:
            with self.subTest(source=source):self.assertIn(code,[d['code'] for d in analyze(source)['diagnostics']])
        self.assertEqual(analyze('var x:Int=0;[x]=input')['diagnostics'],[])
        self.assertEqual(analyze('var x=0;[x]=["unknown remains mutable"];x')['symbols'][0]['type'],'Any')

    def test_analysis_walks_rhs_once_and_preserves_nominal_contracts(self):
        original=Analysis.walk;seen={}
        def measured(this,node,env):seen[id(node)]=seen.get(id(node),0)+1;return original(this,node,env)
        with patch.object(Analysis,'walk',measured):report=analyze('var x=0;[x]=[fn(){tool("inert",null)}()]')
        self.assertEqual(report['diagnostics'],[]);self.assertEqual(max(seen.values()),1)
        self.assertEqual(analyze('class A{}class B{}var item:A=A();[item]=[B()]')['diagnostics'][0]['code'],'type_mismatch')
        self.assertEqual(analyze('class A{}class B(A){}var item:A=A();[item]=[B()]')['diagnostics'],[])

    def test_resource_pattern_types_scope_constructor_and_readonly_leaf(self):
        source='import "std/context" as Context;@Context.contextmanager\nfn* pair(){yield ([20,"yes"])};with pair() as [acquired,label]{acquired};acquired'
        result=analyze(source);symbols={s['name']:s for s in result['symbols']}
        self.assertEqual((symbols['acquired']['type'],symbols['label']['type']),('Int','String'))
        self.assertEqual([d['code'] for d in result['diagnostics']],['undefined_name'])
        self.assertEqual(analyze('with input as [x]{[x]=[1]}')['diagnostics'][0]['code'],'immutable_binding')
        source='data Box{Item(value)};with input as Item(Item){Item}'
        result=analyze(source);self.assertEqual(result['diagnostics'],[])
        ctor=analyze(source,position=source.index('as Item')+4)['hover'];self.assertIn('Item(',ctor['contents'])
        source='import "std/context" as Context;@Context.asynccontextmanager\nasync fn* pair(){yield ([20,"yes"])};async with pair() as [a,b]{[a,b]}'
        result=analyze(source);self.assertEqual(result['diagnostics'],[])
        self.assertEqual({s['name']:s['type'] for s in result['symbols'] if s['name'] in ('a','b')},{'a':'Int','b':'String'})

    def test_symbols_unicode_writes_constructor_reads_and_source_safe_rename(self):
        source='// 🙂 变量\nvar 变量:Int=1;var other=0;[变量,other]=[20,22];{x:变量}={x:42};变量'
        pos=len(source[:source.index('var ')+4].encode('utf-16-le'))//2
        report=references(source,position=pos);self.assertTrue(report['complete'])
        self.assertEqual([r['kind'] for r in report['references']],['declaration','write','write','read'])
        renamed=rename(source,position=pos,new_name='答案')['files']['main.chaos']
        self.assertIn('// 🙂 变量',renamed);self.assertIn('{x:答案}',renamed);self.assertEqual(Evaluator().run(renamed),42)
        source='data Box{Item(value)};var x=0;Item(x)=Item(42);x'
        self.assertEqual(len(references(source,position=source.index('Item'))['references']),3)
        self.assertEqual([r['kind'] for r in references(source,position=source.index('var x')+4)['references']],['declaration','write','read'])

    def test_resource_symbol_shadowing_and_nested_manager_references(self):
        source='let outer=1;with input as [outer],outer as resource{outer};outer'
        outer=references(source,position=source.index('let outer')+4)
        self.assertEqual(len(outer['references']),2)
        local=references(source,position=source.index('[outer]')+1)
        self.assertEqual([r['kind'] for r in local['references']],['declaration','read','read'])
        changed=rename(source,position=source.index('[outer]')+1,new_name='inside')['files']['main.chaos']
        self.assertEqual(changed,'let outer=1;with input as [inside],inside as resource{inside};outer')
        files={'lib.chaos':'let exported=42;export {exported}'}
        source='import "lib.chaos" as L;var x=0;[x]=[L];x'
        self.assertFalse(references(files['lib.chaos'],file='lib.chaos',files={**files,'main.chaos':source},position=4)['complete'])

    def test_http_sdk_ir_cas_source_and_graph_share_patterns(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('assignment',source=SOURCE,capabilities=[])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='source').result(),42)
                program=client.program(p['id'])['program'];node=program['root']['statements'][2]['value']['items'][0]
                changed=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':node['id'],'field':'value','value':21}])
                self.assertEqual(client.start(p['id'],changed['version'],None,mode='graph').result(),43)
                source=changed['source'];self.assertEqual(client.format_source(source),Framework.format_source(source))
                self.assertEqual(client.analyze(source)['diagnostics'],[])
                pos=source.index('var left')+4;self.assertEqual(client.references(source,position=pos),Framework.references(source,position=pos))
                candidate=client.rename(source,position=pos,new_name='initial');self.assertIn('[initial, right]',candidate['files']['main.chaos'])
                self.assertEqual(app.get_project(p['id'])['version'],changed['version'])
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_check_fmt_ir_run(self):
        with tempfile.TemporaryDirectory()as folder:
            source=Path(folder)/'main.chaos';source.write_text(SOURCE);env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(source)],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)

    def test_actual_lsp_pattern_writes_rename_format_and_hover(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);path=root/'main.chaos';path.write_text(SOURCE);client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(len(SOURCE[:SOURCE.index('[left,right]')+1].encode('utf-16-le'))//2)}
                hover=client.request('textDocument/hover',params);self.assertIn('Int',str(hover))
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),3)
                renamed=client.request('textDocument/rename',{**params,'newName':'initial'});self.assertEqual(len(renamed['documentChanges'][0]['edits']),3)
                formatted=client.request('textDocument/formatting',{'textDocument':{'uri':path.as_uri()},'options':{'tabSize':2,'insertSpaces':True}})
                self.assertEqual(formatted[0]['newText'],format_source(SOURCE))
                tokens=client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}});self.assertTrue(tokens['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
