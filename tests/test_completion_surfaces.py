"""Native normal-completion branches share one syntax and scope across surfaces."""
import copy,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.ide_language import analyze
from chaos.language import Evaluator,LanguageError,Parser
from chaos.program import decode_program,format_program,parse_program,plain,templates
from chaos.source_format import format_source
from chaos.symbols import references,rename
from chaos.server import make_server
ROOT=Path(__file__).resolve().parent.parent
SOURCE='// 🙂 completion branches retain outer functions\nfn add(value:Int)->Int{value+1};let result=for item in []{item}else{try{0}catch error{0}else{add(41)}};result'

class CompletionSurfaces(unittest.TestCase):
    def test_all_wrappers_roundtrip_without_changing_historical_shapes(self):
        cases=[('while false{}else{42}',42),('for x in []{}else{42}',42),
               ('for (x,) in (){}else{42}',42),
               ('async fn* rows(){};async for x in rows(){}else{42}',42),
               ('async fn* rows(){};async for [x] in rows(){}else{42}',42),
               ('try{0}catch e{1}else{42}',42),('try{throw 42}catch e{e.value}else{0}',42),
               ('try{0}catch e{1}else{42}finally{9}',42),(SOURCE,42),
               ('for x in [1]{break}else{42}',None)]
        for source,expected in cases:
            with self.subTest(source=source):
                program=parse_program(source);self.assertEqual(program['schema'],'chaos.program/1')
                formatted=format_program(program)
                self.assertEqual(plain(decode_program(program)),plain(Parser(formatted).program()))
                self.assertEqual(Framework.evaluate(source),expected);self.assertEqual(Framework.evaluate(formatted),expected)
        for source,kind,fields in [('while false{}','while',{'condition','body'}),('for x in []{}','for',{'name','iterable','body'}),('try{}catch e{}','try',{'body','name','handler'})]:
            node=parse_program(source)['root']['statements'][0]
            self.assertEqual(node['kind'],kind);self.assertEqual(set(node)-{'kind','id','span'},fields)
        self.assertIn('loop_else',templates());self.assertIn('try_else',templates())

    def test_decode_rejects_wrong_wrapper_roots_and_nonblock_branches(self):
        bad=[]
        for source,field,replacement in [('while false{}else{}','loop','try{}catch e{}'),('try{}catch e{}else{}','body','while false{}'),('while false{}else{}','otherwise','42'),('try{}catch e{}else{}','otherwise','42'),('try{}finally{}','body','for x in []{}'),('try{}finally{}','cleanup','42')]:
            program=parse_program(source);program['root']['statements'][0][field]=parse_program(replacement)['root']['statements'][0];bad.append(program)
        program=parse_program('while false{}else{}');program['root']['statements'][0]['extra']=True;bad.append(program)
        for program in bad:
            with self.subTest(program=program),self.assertRaises(LanguageError):decode_program(program)
        for source in ['try{}else{}','while false{}else{break}','for x in []{}else{continue}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):parse_program(source)

    def test_completion_result_inference_excludes_protected_value(self):
        source='let result=try{"discarded"}catch e{41}else{42};result'
        self.assertEqual(next(x['type']for x in analyze(source)['symbols']if x['name']=='result'),'Int')
        source='let result=try{42}catch e{"handled"}else{false};result'
        self.assertEqual(next(x['type']for x in analyze(source)['symbols']if x['name']=='result'),'Any')
        for branch,typ in [('42','Any'),('null','Null')]:
            source='let result=for x in []{}else{'+branch+'};result'
            self.assertEqual(next(x['type']for x in analyze(source)['symbols']if x['name']=='result'),typ)
        source='fn f(x:Int)->Int{x};let result=try{f}catch e{f}else{f};result('
        self.assertEqual(analyze(source,position=len(source))['signatureHelp']['parameters'],[])
        source='fn f(x:Int)->Int{x};var target=f;for x in []{}else{target=0};target('
        self.assertIsNone(analyze(source,position=len(source))['signatureHelp'])

    def test_else_uses_outer_symbols_and_rename_never_captures_loop_or_catch_names(self):
        for source,expected in [('let value=42;for value in [1]{value}else{value}',42),('let value=42;try{let value=0;value}catch value{value}else{value}',42),('let value=42;while false{let value=0;value}else{value}',42)]:
            with self.subTest(source=source):
                info=references(source,position=source.index('value'));self.assertTrue(info['complete']);self.assertEqual(len(info['references']),2)
                changed=rename(source,position=source.index('value'),new_name='outer')['files']['main.chaos']
                self.assertIn('else{outer}',changed);self.assertIn('value',changed);self.assertEqual(Framework.evaluate(changed),expected)
        for source,name in [('for item in []{}else{item}','item'),('try{let inner=1}catch error{}else{inner}','inner'),('try{}catch caughtIssue{}else{caughtIssue}','caughtIssue')]:
            diagnostics=analyze(source)['diagnostics'];self.assertTrue(any(x['code']=='undefined_name' and name in x['message']for x in diagnostics),diagnostics)
        source='fn add(value:Int)->Int{value};for item in []{}else{add('
        self.assertIn('value: Int',analyze(source,position=len(source))['signatureHelp']['label'])
        self.assertNotIn('item',{x['label']for x in analyze('for item in []{}else{ite',position=24)['completions']})

    def test_formatter_preserves_comments_nested_association_and_tail_expressions(self):
        cases=[SOURCE,'for x in []{// body\n}// adjacent\nelse{// completion\n42}',
               'for x in []{if true{1}else{2}}else{try{0}catch e{1}else{42}finally{null}}',
               'fn f(){return while false{}else{42}};f()',
               'var n=0;for x in [1]{while false{}else{n=42;break}}else{n=0};n',
               'async fn* rows(){};async for (item,) in rows(){}else{42}',
               'let x={answer:42};try{0}catch e{1}else{x["answer"]}']
        for source in cases:
            for width in (40,88):
                with self.subTest(source=source,width=width):
                    version=1 if 'x["answer"]'in source else 2
                    formatted=format_source(source,language_version=version,line_width=width)
                    self.assertEqual(format_source(formatted,line_width=width),formatted)
                    self.assertEqual(plain(Parser(source,legacy_items=version==1).program()),plain(Parser(formatted).program()))
                    if version==2:self.assertEqual(Framework.evaluate(source),Framework.evaluate(formatted))
        self.assertIn('// adjacent',format_source(cases[1]))
        self.assertIn('for item in []',format_source(SOURCE))

    def test_analysis_format_and_symbols_never_execute_completion_effects(self):
        source='for item in []{}else{tool("must not execute",42)};try{0}catch e{e}else{tool("never",1)}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated source')):
            self.assertEqual(analyze(source)['diagnostics'],[]);self.assertIn('else',format_source(source))
            self.assertTrue(references(source,position=source.index('item'))['complete'])

    def test_http_sdk_structural_edit_conflict_and_run_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('completion-surfaces',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program'];literal=program['root']['statements'][1]['value']['otherwise']['statements'][0]['otherwise']['statements'][0]['arguments'][0]
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':literal['id'],'field':'value','value':42}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source'])['diagnostics'],[]);self.assertEqual(client.format_source(updated['source']),format_source(updated['source']))
                with self.assertRaises(Exception):client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':literal['id'],'field':'value','value':0}])
                self.assertEqual(client.get_project(project['id'])['version'],updated['version'])
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_lsp_scope_rename_hover_and_semantic_tokens(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);path=root/'main.chaos';path.write_text(SOURCE);env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)
            client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                position=len(SOURCE[:SOURCE.index('add(41')].encode('utf-16-le'))//2
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(position)}
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),2)
                change=client.request('textDocument/rename',{**params,'newName':'increment'});self.assertEqual(len(change['documentChanges'][0]['edits']),2)
                self.assertIn('add(value: Int) -> Int',str(client.request('textDocument/hover',params)))
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
