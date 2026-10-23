"""Nominal stored-field patterns use the same source and structural language."""
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
SOURCE='// 🙂 class patterns project stored contracts without getters\nclass Box{let left:Int=41;let right:Int=42};fn add(value:Int)->Int{value+1};let Box::{left:value}=Box();add(value)'
PREFIX='class Box{let value:Int=42};'

class ClassPatternSurfaces(unittest.TestCase):
    def test_native_ir_and_canonical_roundtrip_all_pattern_sites(self):
        cases=[PREFIX+'let Box::{value:answer}=Box();answer',PREFIX+'var answer=0;Box::{value:answer}=Box();answer',
               PREFIX+'fn f(item as Box::{value:answer},/){answer};f(Box())',
               PREFIX+'for Box::{value:answer} in [Box()]{answer}',
               PREFIX+'[answer for Box::{value:answer} in [Box()]]',
               PREFIX+'{str(answer):answer for Box::{value:answer} in [Box()]}',
               PREFIX+'collect((answer for Box::{value:answer} in [Box()]))',
               PREFIX+'match Box(){Box::{value:answer}=>answer}',
               PREFIX+'try{throw Box()}catch {value:Box::{value:answer},.._}{answer}',
               PREFIX+'async fn* rows(){yield Box()};async for Box::{value:answer} in rows(){answer}',
               PREFIX+'async fn* rows(){yield Box()};[answer async for Box::{value:answer} in rows()]',
               PREFIX+'class C{fn __enter__(self){Box()}fn __exit__(self,e){false}};with C() as Box::{value:answer}{answer}',
               PREFIX+'class C{async fn __aenter__(self){Box()}async fn __aexit__(self,e){false}};async with C() as Box::{value:answer}{answer}']
        for source in cases:
            with self.subTest(source=source):
                program=parse_program(source);formatted=format_program(program)
                self.assertEqual(plain(decode_program(program)),plain(Parser(formatted).program()))
                self.assertEqual(Framework.evaluate(formatted),Framework.evaluate(source));self.assertEqual(analyze(source)['diagnostics'],[])
                at=source.index('answer');changed=rename(source,position=at,new_name='result')['files']['main.chaos']
                self.assertEqual(Framework.evaluate(changed),Framework.evaluate(source));self.assertNotIn('answer',changed)
        self.assertIn('p_class',templates());self.assertEqual(templates()['p_class']['name'],'Example')

    def test_closed_ir_names_duplicates_rest_and_labels(self):
        base=parse_program(PREFIX+'let Box::{value:answer}=Box();answer');bad=[]
        for change in [lambda p:p.update(name='x..y'),lambda p:p.update(name=''),lambda p:p.update(extra=True),
                       lambda p:p['fields'].append(copy.deepcopy(p['fields'][0])),
                       lambda p:p.update(rest={'id':'bad_rest','kind':'p_literal','value':1}),
                       lambda p:p['fields'][0].update(value={'id':'bad_value','kind':'literal','value':1})]:
            program=copy.deepcopy(base);change(program['root']['statements'][1]['pattern']);bad.append(program)
        for program in bad:
            with self.subTest(program=program),self.assertRaises(LanguageError):decode_program(program)
        for source in [PREFIX+'match Box(){Box::{}=>42}',PREFIX+'let Box::{..rest}=Box();rest.value',PREFIX+'let Box::{"value":answer,.._}=Box();answer']:
            self.assertEqual(Framework.evaluate(format_program(parse_program(source))),42)

    def test_outer_class_and_leaf_rename_keep_field_labels_and_shadowing(self):
        source=PREFIX+'do{let Box::{value:Box}=Box();Box}'
        report=references(source,position=source.index('Box'));self.assertTrue(report['complete']);self.assertEqual(len(report['references']),3)
        changed=rename(source,position=source.index('Box'),new_name='Container')['files']['main.chaos'];self.assertIn('Container::{value:Box}',changed);self.assertEqual(Framework.evaluate(changed),42)
        at=source.index('value:Box')+6;changed=rename(source,position=at,new_name='answer')['files']['main.chaos'];self.assertIn('Box::{value:answer}',changed);self.assertEqual(Framework.evaluate(changed),42)
        files={'lib.chaos':PREFIX+'export{Box}','main.chaos':'import "./lib.chaos" as lib;let lib.Box::{value:answer}=lib.Box();answer'}
        source=files['main.chaos'];changed=rename(source,file='main.chaos',files=files,position=source.index('Box'),new_name='Container')['files']
        self.assertIn('class Container',changed['lib.chaos']);self.assertIn('lib.Container::{value:answer}',changed['main.chaos'])

    def test_stored_contract_hints_shared_match_binder_and_inert_analysis(self):
        source='class Base{let field:Int=42;@property\nfn computed(self)->String{throw "never"}};class Child(Base){};match Child(){Base::{field:value,computed:unknown,..rest}=>value}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated')):
            result=analyze(source);self.assertEqual(result['diagnostics'],[]);format_source(source)
            kinds={s['name']:s['type'] for s in result['symbols']};self.assertEqual(kinds['value'],'Int');self.assertEqual(kinds['unknown'],'Any');self.assertEqual(kinds['rest'],'Record')
            self.assertTrue(references(source,position=source.index('Base::{'))['complete'])
        source='class Base{let x:Int=1};class Sub(Base){let x:String="yes"};fn f(v as Base::{x:n}){n+"!"};f(Sub())'
        self.assertEqual(analyze(source)['diagnostics'],[]);self.assertEqual(Framework.evaluate(source),'yes!')
        source='class Base{let x:Int=1};class Sub(Base){let x:String="yes"};let Base::{x:n}=Sub();n'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='n'),'String')
        source='class C{let worker=fn(value:Int)->Int{value}};let C::{worker:run}=C();run('
        self.assertIn('value: Int',analyze(source,position=len(source))['signatureHelp']['label'])
        result=analyze(PREFIX+'match Box(){Box::{value:answer}=>answer};answer')
        self.assertTrue(any(d['code']=='undefined_name' for d in result['diagnostics']))
        self.assertEqual(analyze('match 1{[unused]=>0, _=>42}')['diagnostics'],[])

    def test_source_formatter_comments_width_nested_patterns_and_slice_compatibility(self):
        sources=[SOURCE,PREFIX+'let Box : // first colon\n : {value:answer,.._}=Box();answer',
                 PREFIX+'match Box(){Box::{value:answer} if true=>answer,_=>0}',
                 'class C{let payload={long_field_name:42}};let C::{payload:{long_field_name:answer,.._},..rest}=C();answer',
                 'class C{let value=42};try{throw C()}catch {value:C::{value:answer},.._}{answer}',
                 'let a=[1,2,3,4];a[::2]', 'let a=[1,2,3,4];a[1:3:1]']
        for source in sources:
            for version in ((2,) if source.startswith('let a=') else (1,2)):
                for width in (40,88):
                    with self.subTest(source=source,version=version,width=width):
                        formatted=format_source(source,language_version=version,line_width=width)
                        self.assertEqual(format_source(formatted,line_width=width),formatted)
                        self.assertEqual(plain(Parser(source,legacy_items=version==1).program()),plain(Parser(formatted).program()))
        self.assertIn('Box::{left: value}',format_source(SOURCE));self.assertIn('// first colon',format_source(sources[1]))

    def test_http_sdk_structural_field_edit_and_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('class-pattern-surfaces',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program'];node=program['root']['statements'][2]['pattern'];fields=copy.deepcopy(node['fields']);fields[0]['key']='right'
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':node['id'],'field':'fields','value':fields}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43);self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source'])['diagnostics'],[]);self.assertEqual(client.format_source(updated['source']),format_source(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_lsp_class_reference_rename_hover_and_tokens(self):
        from tests.test_lsp_integration import StdioClient
        from chaos.lsp_workspace import TextMap
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);path=root/'main.chaos';path.write_text(SOURCE);env=dict(os.environ,PYTHONPATH=str(ROOT))
            for command in ('check','fmt','ir','run'):
                result=subprocess.run([sys.executable,'-m','chaos',command,str(path)],cwd=folder,env=env,capture_output=True,text=True,timeout=10);self.assertEqual(result.returncode,0,result.stderr)
                if command=='run':self.assertEqual(json.loads(result.stdout),42)
            client=StdioClient(root)
            try:
                client.send('textDocument/didOpen',{'textDocument':{'uri':path.as_uri(),'version':1,'languageId':'chaos','text':SOURCE}})
                at=len(SOURCE[:SOURCE.index('Box::{')].encode('utf-16-le'))//2;params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(at)}
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),3)
                change=client.request('textDocument/rename',{**params,'newName':'Container'});self.assertEqual(len(change['documentChanges'][0]['edits']),3)
                at=len(SOURCE[:SOURCE.index('left:value')+5].encode('utf-16-le'))//2;params['position']=TextMap(SOURCE).position(at)
                self.assertIn('Int',str(client.request('textDocument/hover',params)));self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
