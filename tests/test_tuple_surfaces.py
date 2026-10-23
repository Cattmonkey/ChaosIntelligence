"""Tuple syntax, semantic tooling and structural/headless execution agree."""
import copy,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.ide_language import analyze
from chaos.language import Evaluator,LanguageError,Parser
from chaos.program import decode_program,format_program,parse_program,plain
from chaos.source_format import format_source
from chaos.symbols import references,rename
from chaos.server import make_server
ROOT=Path(__file__).resolve().parent.parent
SOURCE='// 🙂 tuple preserves callable identity and typed leaves\nfn add(value:Int)->Int{value+1};let (worker,value):Tuple=(add,41);worker(value)'
KEY_SOURCE='class Grid{fn __getitem__(self,key:Tuple){let (row,column)=key;row+column}};Grid()[20,22]'

class TupleSurfaces(unittest.TestCase):
    def test_tuple_ir_roundtrips_and_contextual_composite_keys(self):
        for source in ['()', '(42,)', '((1,2),3)', SOURCE, KEY_SOURCE,
                       'class Grid{fn __getitem__(self,key){type_of(key)}};Grid()[1:3,2]',
                       'class Grid{fn __getitem__(self,key){type_of(key)}};Grid()[1,]',
                       'class Grid{fn __getitem__(self,key){type_of(key)}};Grid()[()]',
                       'class Grid{fn __getitem__(self,key){type_of(key)}};Grid()[(1,2)]']:
            with self.subTest(source=source):
                program=parse_program(source);self.assertEqual(program['schema'],'chaos.program/1')
                formatted=format_program(program)
                self.assertEqual(plain(decode_program(program)),plain(Parser(formatted).program()))
                self.assertEqual(Framework.evaluate(source),Framework.evaluate(formatted))
        self.assertIn('[1: 3, 2]',format_source('value[1:3,2]'))

    def test_ir_slice_only_direct_key_component_and_pattern_validation(self):
        program=parse_program('target[1:3,2]');key=program['root']['statements'][0]['index']
        self.assertEqual(key['kind'],'tuple');self.assertEqual(key['items'][0]['kind'],'slice')
        variants=[]
        bad=copy.deepcopy(program);bad['root']['statements']=[bad['root']['statements'][0]['index']];variants.append(bad)
        bad=copy.deepcopy(program);key=bad['root']['statements'][0]['index'];key['items']=[{'id':'nested_key','kind':'tuple','items':key['items']}];variants.append(bad)
        bad=copy.deepcopy(program);key=bad['root']['statements'][0]['index'];key['items']=[{'id':'nested_list','kind':'list','items':key['items']}];variants.append(bad)
        bad=parse_program('let (x,)=(1,)');bad['root']['statements'][0]['pattern']['rest']=parse_program('match 0{0=>0}')['root']['statements'][0]['arms'][0]['pattern'];variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad),self.assertRaises(LanguageError):decode_program(bad)
        for source in ['(1:3,2)','target[((1:3,2),)]','let (x,x)=(1,2)']:
            with self.subTest(source=source),self.assertRaises(LanguageError):parse_program(source)

    def test_all_tuple_pattern_sites_and_symbol_rename(self):
        cases=[('let (answer,..rest)=(42,9);answer','answer'),
               ('var (answer,other)=(1,2);(answer,other)=(42,0);answer','answer'),
               ('fn f(pair as (answer,):Tuple){answer};f((42,))','answer'),
               ('for (answer,) in ((42,),){answer}','answer'),
               ('[answer for (answer,) in ((42,),)]','answer'),
               ('match (42,){(answer,)=>answer}','answer'),
               ('async fn* rows(){yield (42,)};async for (answer,) in rows(){answer}','answer'),
               ('async fn* rows(){yield (42,)};[answer async for (answer,) in rows()]','answer'),
               ('class C{async fn __aenter__(self){(42,)}async fn __aexit__(self,e){false}};async with C() as (answer,){answer}','answer'),
               ('class C{fn __enter__(self){(42,)}fn __exit__(self,e){false}};with C() as (answer,){answer}','answer')]
        for source,name in cases:
            with self.subTest(source=source):
                formatted=format_program(parse_program(source));self.assertEqual(Framework.evaluate(formatted),Framework.evaluate(source))
                result=analyze(source);self.assertEqual(result['diagnostics'],[])
                report=references(source,position=source.index(name));self.assertTrue(report['complete'])
                changed=rename(source,position=source.index(name),new_name='renamed')['files']['main.chaos']
                self.assertEqual(Framework.evaluate(changed),Framework.evaluate(source))
                self.assertNotIn(name,changed)

    def test_conservative_callable_projection_iterables_and_mutable_invalidation(self):
        sources=['fn add(value:Int)->Int{value};let (worker,)= (add,);worker(',
                 'fn add(value:Int)->Int{value};let pair=(add,);pair[0](',
                 'fn add(value:Int)->Int{value};let pair=(add,);pair[-1](',
                 'fn add(value:Int)->Int{value};let pair=tuple([add]);pair[0](']
        for source in sources:
            result=analyze(source,position=len(source));self.assertIsNotNone(result['signatureHelp'],source)
            self.assertIn('value: Int',result['signatureHelp']['label'])
        hint=analyze('tuple(',position=6)['signatureHelp'];self.assertIn('items',hint['label']);self.assertIn('Tuple',hint['label'])
        source='fn add(value:Int)->Int{value};var pair:Tuple=(add,);pair=(1,);pair[0]('
        self.assertIsNone(analyze(source,position=len(source))['signatureHelp'])
        for source in ['let (x,..rest)=(42,1);type_of(rest)','collect((1,2))','map(fn(x){x+1},(1,2))','tuple(items=(1,2))']:
            self.assertEqual(analyze(source)['diagnostics'],[],source)
        self.assertTrue(analyze('let (x,)=[1];x')['diagnostics'])
        self.assertTrue(analyze('let [x]=(1,);x')['diagnostics'])
        result=analyze(SOURCE);self.assertEqual(result['diagnostics'],[])
        self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='worker'),'Function')

    def test_formatter_preserves_comments_singletons_groups_and_generators(self):
        sources=[SOURCE,'let pair=(42, // keep comma\n);pair',
                 'let rows=((1,2),(3,4));[a+b for (a,b) in rows]',
                 'let g=(x for x in (1,2));collect(g)',
                 'fn f(pair as (value,..rest):Tuple,/){(value,rest)};f((1,2))',
                 'class G{fn __getitem__(self,key){key}};G()[1:3,2:5:2]',
                 'let (left,right)=((20),(22));left+right']
        for source in sources:
            for width in (40,88):
                with self.subTest(source=source,width=width):
                    formatted=format_source(source,line_width=width)
                    self.assertEqual(format_source(formatted,line_width=width),formatted)
                    self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
                    self.assertEqual(Framework.evaluate(source),Framework.evaluate(formatted))
        self.assertEqual(format_source('(42,)').strip(),'(42,)')

    def test_analyzer_and_formatter_do_not_run_tuple_protocols(self):
        source='class Items{fn __iter__(self){throw "must not run"}};let values=tuple(Items());values'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis ran source')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('analysis evaluated source')):
            self.assertEqual(analyze(source)['diagnostics'],[]);self.assertIn('tuple',format_source(source))

    def test_http_sdk_ir_edit_and_snapshot_preservation(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('tuple-surfaces',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program'];literal=program['root']['statements'][1]['value']['items'][1]
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':literal['id'],'field':'value','value':42}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43)
                self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source'])['diagnostics'],[])
                self.assertEqual(client.format_source(updated['source']),format_source(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_and_real_lsp_tuple_leaf_refs_and_hover(self):
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
                position=len(SOURCE[:SOURCE.index('worker,value')].encode('utf-16-le'))//2
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(position)}
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),2)
                change=client.request('textDocument/rename',{**params,'newName':'renamed'});self.assertEqual(len(change['documentChanges'][0]['edits']),2)
                self.assertIn('Function',str(client.request('textDocument/hover',params)))
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
