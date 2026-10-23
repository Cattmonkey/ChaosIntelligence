"""Native immutable sets and eager comprehensions share all authoring paths."""
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
SOURCE='// 🙂 sets preserve typed uniqueness and shared lexical scopes\nfn total(values:Set)->Int{fold(fn(acc,item){acc+item},0,values)};let choices:Set={*[],41,41};let mapped:Set={value+1 for value in set_add(choices,41)};total(mapped)'

class SetSurfaces(unittest.TestCase):
    def test_literal_spread_comprehensions_both_dialects_roundtrip(self):
        cases=[SOURCE,'{*[]}','{42}','{42,}','{true,1,1.0}', 'collect({*(20,21),22,20})',
               'collect({value*2 for value in {20,21,20} if value>20})',
               'class Box{let value:Int=42};collect({answer for Box::{value:answer} in [Box()]})',
               'collect({a+b for (a,b) in {(20,22),(21,21)}})',
               'async fn* rows(){yield 20;yield 21};collect({value*2 async for value in rows()})',
               'async fn work(x){x+1};collect({await work(x) for x in {40,41}})',
               'let set_of=fn(){999};type_of({*[]})','set({answer:41},"answer",42).answer']
        for source in cases:
            for version in (1,2):
                with self.subTest(source=source,version=version):
                    program=parse_program(source,language_version=version);formatted=format_program(program)
                    self.assertEqual(plain(decode_program(program)),plain(Parser(formatted).program()))
                    self.assertEqual(Framework.evaluate(formatted),Framework.evaluate(source));self.assertEqual(analyze(source)['diagnostics'],[])
        self.assertEqual(templates()['set']['kind'],'set');self.assertEqual(templates()['set_comp']['kind'],'set_comp')
        self.assertEqual(parse_program('{}')['root']['statements'][0]['kind'],'record')

    def test_ir_rejects_empty_set_and_spread_outside_direct_contexts(self):
        base=parse_program('{*[],1}');bad=[]
        p=copy.deepcopy(base);p['root']['statements'][0]['items']=[];bad.append(p)
        p=copy.deepcopy(base);p['root']['statements']=[p['root']['statements'][0]['items'][0]];bad.append(p)
        p=copy.deepcopy(base);p['root']['statements'][0]['kind']='list';bad.append(p)
        p=copy.deepcopy(base);p['root']['statements'][0]['items'][0]['kind']='kwspread';bad.append(p)
        p=copy.deepcopy(base);p['root']['statements'][0]['items'][0]['value']=copy.deepcopy(p['root']['statements'][0]['items'][0]);p['root']['statements'][0]['items'][0]['value']['id']='inner_spread';bad.append(p)
        p=parse_program('{x for x in [1]}');p['root']['statements'][0]['element']=base['root']['statements'][0]['items'][0];bad.append(p)
        p=parse_program('{x for x in [1]}');p['root']['statements'][0]['clauses']=[];bad.append(p)
        p=parse_program('{x for x in [1] if true}');p['root']['statements'][0]['clauses'].reverse();bad.append(p)
        for program in bad:
            with self.subTest(program=program),self.assertRaises(LanguageError):decode_program(program)
        for source in ['{**{}}','{*[] for x in []}','{1,x:2}','{x:2,1}']:
            with self.subTest(source=source),self.assertRaises(LanguageError):parse_program(source)
        for source in ['fn f(*args){args};f(*{1,2})','fn f(**args){args};f(**{x:1})','{*(1,2),3}']:
            format_program(parse_program(source))

    def test_scope_rename_spread_and_shadowed_builtin_identity(self):
        source='let value=10;let result={value+offset for value in {20,21} for offset in {value,22} if offset>0};[collect(result),value]'
        at=source.index('for value')+4;report=references(source,position=at);self.assertTrue(report['complete']);self.assertEqual(len(report['references']),3)
        changed=rename(source,position=at,new_name='candidate')['files']['main.chaos'];self.assertIn('let value=10',changed);self.assertEqual(Framework.evaluate(changed),Framework.evaluate(source))
        source='let items={20,22};collect({*items})';changed=rename(source,position=source.index('items'),new_name='values')['files']['main.chaos'];self.assertIn('*values',changed);self.assertEqual(Framework.evaluate(changed),[20,22])
        source='fn set_of(items){"custom"};let result=set_of([1]);result';self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='result'),'Any');self.assertEqual(Framework.evaluate(source),'custom')
        self.assertTrue(any(d['code']=='undefined_name' for d in analyze('let values={x for x in {1}};x')['diagnostics']))

    def test_inert_set_type_element_hints_native_signatures_and_updates(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated')):
            result=analyze(SOURCE);self.assertEqual(result['diagnostics'],[]);format_source(SOURCE)
            types={s['name']:s['type'] for s in result['symbols']};self.assertEqual(types['choices'],'Set');self.assertEqual(types['mapped'],'Set');self.assertEqual(types['value'],'Int')
            hint=analyze('set_add(value={1},item=',position=len('set_add(value={1},item='))['signatureHelp'];self.assertIn('value: Set',hint['label']);self.assertIn('item',hint['label'])
        for source in ['let s={1}|{2};s','let s={1}&{2};s','let s={1}-{2};s','let s={1}^{2};s','let s=set_discard({1},1);s','let s=set_of(items=(1,2));s']:
            self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='s'),'Set')
        source='var values:Set={1};values={"x"};{x for x in values}'
        self.assertEqual(next(s['type'] for s in analyze(source)['symbols'] if s['name']=='x'),'Any')
        self.assertEqual(analyze('fn collect_all(values:Set){apply(fn(*xs){xs},values)}')['diagnostics'],[])
        source='let s0={1};'+''.join('let s'+str(i)+'={*s'+str(i-1)+',*s'+str(i-1)+'};' for i in range(1,18))+'{x for x in s17}'
        result=analyze(source);self.assertEqual(result['diagnostics'],[]);self.assertEqual(next(s['type'] for s in result['symbols'] if s['name']=='x'),'Any')

    def test_formatter_braces_comments_nested_scopes_and_record_compatibility(self):
        cases=[SOURCE,'macro values(){let value={1,true,1.0,1,(20,22),{2,3},{3,2}};syntax_literal((value,set_of("aba"),{*(),*[],*value,42}))};values!()', 'let values={1, // preserve first\n*{2,3},4};collect(values)',
               'collect({if true{42}else{0},try{1}catch e{0}})',
               'let values={(20,{1,2}),(22,{2,1})};collect(values)',
               'let values={long_value+offset for long_value in {1,2} for offset in {20,21} if offset>0};collect(values)',
               'let record={if:1,for:2};record.["if"]+record.["for"]',
               'let values={*[]};let record={};[type_of(values),type_of(record)]']
        for source in cases:
            for version in (1,2):
                for width in (40,88):
                    with self.subTest(source=source,version=version,width=width):
                        formatted=format_source(source,language_version=version,line_width=width);self.assertEqual(format_source(formatted,line_width=width),formatted)
                        self.assertEqual(plain(Parser(source,legacy_items=version==1).program()),plain(Parser(formatted).program()))
        self.assertIn('// preserve first',format_source(cases[2]));self.assertEqual(format_source('{*[]}').strip(),'{*[]}')

    def test_http_sdk_structural_set_head_edit_and_snapshot(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('set-surfaces',source=SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program'];node=program['root']['statements'][2]['value']['element']['right']
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':node['id'],'field':'value','value':2}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43);self.assertEqual(old.get()['snapshot']['source'],SOURCE)
                self.assertEqual(client.analyze(updated['source'])['diagnostics'],[]);self.assertEqual(client.format_source(updated['source']),format_source(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_lsp_comprehension_reference_rename_hover_and_tokens(self):
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
                at=len(SOURCE[:SOURCE.index('for value')+4].encode('utf-16-le'))//2;params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(at)}
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),2)
                change=client.request('textDocument/rename',{**params,'newName':'candidate'});self.assertEqual(len(change['documentChanges'][0]['edits']),2)
                self.assertIn('Int',str(client.request('textDocument/hover',params)));self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
