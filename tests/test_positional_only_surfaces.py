"""Private positional slots retain one contract across every authoring surface."""
import copy,json,os,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.ide_language import analyze
from chaos.language import Evaluator,LanguageError,Parser
from chaos.program import decode_program,format_program,parse_program,patch_program,plain
from chaos.source_format import format_source
from chaos.symbols import references,rename
from chaos.server import make_server

ROOT=Path(__file__).resolve().parent.parent
SOURCE='// 🙂 value is a private slot; keyword value remains data\nfn combine(value:Int,/,extra:Int=2,**options)->Int{value+extra+get(options,"value",0)};combine(39,value=1)'

class PositionalOnlySurfaces(unittest.TestCase):
    def test_extended_ir_rows_slash_boundaries_and_all_function_flavors(self):
        for prefix,body,call in [('fn','value','f(42)'),('fn*','yield value','next(f(42)).value'),('async fn','value','await f(42)'),('async fn*','yield value','(await anext(f(42))).value')]:
            source=prefix+' f(value:Int,/){'+body+'};'+call
            program=parse_program(source);params=program['root']['statements'][0]['function']['parameters']
            self.assertEqual(params,[{'name':'value','type':'Int','mode':'positional_only','default':None}])
            for text in (format_program(program),format_source(source)):
                self.assertEqual(plain(Parser(text).program()),plain(decode_program(program)));self.assertEqual(Evaluator().run(text),42)
        source='fn f(value as [left,right]:List=[20,22],/,*,flag=true){left+right};f()'
        self.assertEqual(Evaluator().run(format_program(parse_program(source))),42)
        self.assertEqual(parse_program('fn f(x){x}')['root']['statements'][0]['function']['parameters'],[{'name':'x','type':'Any'}])

    def test_ir_rejects_nonprefix_modes_rest_order_and_cross_boundary_defaults(self):
        base=parse_program('fn f(a,b,c){a}')
        cases=[['positional','positional_only','positional'],['keyword_only','positional_only','varkw'],['varargs','varargs','varkw'],['varkw','positional','keyword_only'],['keyword_only','varargs','varkw']]
        for modes in cases:
            candidate=copy.deepcopy(base)
            for row,mode in zip(candidate['root']['statements'][0]['function']['parameters'],modes):row.update(mode=mode,default=None)
            with self.subTest(modes=modes),self.assertRaises(LanguageError):decode_program(candidate)
        bad=parse_program('fn f(a=1,/,b=2){a}');bad['root']['statements'][0]['function']['parameters'][1]['default']=None
        with self.assertRaises(LanguageError):decode_program(bad)
        bad=copy.deepcopy(base);bad['root']['statements'][0]['function']['parameters'][0]['name']='reserved.invalid'
        with self.assertRaises(LanguageError):decode_program(bad)

    def test_source_formatter_separator_not_division_comments_or_strings(self):
        sources=['fn f(value:Int,/,extra=4/2){value+extra};f(40)',
                 'fn f(value=20/2, // / marker follows\n /, other=32){value+other};f()',
                 'fn f(value as {x:item}:Record={x:42},/){item};f()',
                 'fn f(value, /,){value};f("/, /")',
                 'fn f(value=fn(a,/){a},/){value(42)};f()']
        for source in sources:
            for width in (40,88):
                with self.subTest(source=source,width=width):
                    text=format_source(source,line_width=width);self.assertEqual(format_source(text,line_width=width),text)
                    self.assertEqual(plain(Parser(text).program()),plain(Parser(source).program()));self.assertEqual(Evaluator().run(text),Evaluator().run(source))
        self.assertIn('value: Int, /, extra = 4 / 2',format_source(sources[0]))
        self.assertNotIn('/ ,',format_source(sources[0]))

    def test_signature_bound_receiver_native_and_parameter_count(self):
        prefixes=[('fn f(a,/,b=2,*,c=3){a};f(',['a','b','c'],'a, /, b'),
                  ('class C{fn f(self,/,a){a}}let c=C();c.f(',['a'],'f(a)'),
                  ('class C{fn f(self,a,/,b=2){a}}let c=C();c.f(',['a','b'],'f(a, /, b'),
                  ('class C{fn __init__(self,value,/,**kw){null}};C(',['value','kw'],'C(value, /, **kw)'),
                  ('let s=exit_stack();s.callback(',['function','args','kwargs'],'Function, /, *args')]
        for source,names,label in prefixes:
            with self.subTest(source=source):
                result=analyze(source)['signatureHelp'];self.assertIsNotNone(result);self.assertEqual([p['name'] for p in result['parameters']],names);self.assertIn(label,result['label'])
        result=analyze('fn f(value,/,**kw){value};f(1,value=')['signatureHelp']
        self.assertEqual(result['activeParameter'],1);self.assertEqual(result['parameters'][0]['mode'],'positional_only')

    def test_completion_counts_private_position_and_does_not_offer_keyword(self):
        prefix='fn f(first,/,second=2,third=3){first};f('
        result=analyze(prefix+'1,')
        labels={p['label'] for p in result['completions']}
        self.assertIn('second=',labels);self.assertIn('third=',labels);self.assertNotIn('first=',labels)
        self.assertEqual(result['signatureHelp']['activeParameter'],1)
        result=analyze(prefix+'1,2,');labels={p['label'] for p in result['completions']}
        self.assertIn('third=',labels);self.assertNotIn('second=',labels)
        self.assertEqual(analyze('fn f(first=1,/,**kw){first};f(first=2)')['diagnostics'],[])
        self.assertTrue(analyze('fn f(first,/,**kw){first};f(first=2)')['diagnostics'])
        self.assertTrue(analyze('fn f(first,/){first};f(1,first=2)')['diagnostics'])

    def test_rename_private_slot_keeps_equal_spelled_keyword_and_record_keys(self):
        source='// 🙂 名称\nfn f(名称:Int,/,**kw){[名称,kw]};f(42,名称=9)'
        position=len(source[:source.index('名称:Int')].encode('utf-16-le'))//2
        report=references(source,position=position);self.assertTrue(report['complete']);self.assertEqual([r['kind'] for r in report['references']],['declaration','read'])
        renamed=rename(source,position=position,new_name='答案')['files']['main.chaos']
        self.assertEqual(renamed,'// 🙂 名称\nfn f(答案:Int,/,**kw){[答案,kw]};f(42,名称=9)')
        self.assertEqual(Evaluator().run(renamed),[42,{'名称':9}])
        self.assertIsNone(references(source,position=len(source[:source.rindex('名称=9')].encode('utf-16-le'))//2)['target'])
        # Rest variable names also do not name keyword entries.
        source='fn f(*items,**options){[items,options]};f(1,items=2,options=3)'
        for name in ('items','options'):
            changed=rename(source,position=source.index(name),new_name='collected')['files']['main.chaos']
            self.assertIn('items=2,options=3',changed);self.assertEqual(Evaluator().run(changed),[[1],{'items':2,'options':3}])

    def test_cross_module_alias_rename_and_dynamic_conservative_boundary(self):
        files={'lib.chaos':'fn f(value:Int,/,**kw){value};export{f}','main.chaos':'import "lib.chaos" as L;let alias=L.f;alias(42,value=9)'}
        change=rename(files['lib.chaos'],file='lib.chaos',files=files,position=5,new_name='private')
        self.assertEqual(change['files']['main.chaos'],files['main.chaos']);self.assertEqual(Evaluator().run(change['files']['main.chaos'],files=change['files']),42)
        source='fn f(value,/,**kw){value};let box={call:f};box.call(42,value=9)'
        self.assertTrue(references(source,position=source.index('value'))['complete'])
        changed=rename(source,position=source.index('value'),new_name='private')['files']['main.chaos']
        self.assertIn('box.call(42,value=9)',changed);self.assertEqual(Evaluator().run(changed),42)
        public='fn f(value){value};let box={call:f};box.call(value=42)'
        self.assertFalse(references(public,position=public.index('value'))['complete'])
        with self.assertRaises(LanguageError):rename(public,position=public.index('value'),new_name='private')

    def test_inert_analysis_contract_and_format_without_defaults_or_callbacks(self):
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('analysis evaluated')):
            result=analyze('fn f(value:Int=tool("inert",null),/){value};f()');self.assertEqual(result['diagnostics'],[])
            self.assertIn('/',format_source('fn f(x=tool("inert",null),/){x}'))
            result=analyze(SOURCE,position=len(SOURCE[:SOURCE.index('combine(39,value=')+len('combine(39,value=')].encode('utf-16-le'))//2)
            self.assertEqual(result['diagnostics'],[]);self.assertEqual(result['signatureHelp']['activeParameter'],2)

    def test_http_sdk_ir_mode_edit_cas_and_headless_execution(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));p=client.create_project('private-params',source=SOURCE,capabilities=[])
                self.assertEqual(client.start(p['id'],p['version'],None,mode='source').result(),42)
                program=client.program(p['id'])['program'];fn=program['root']['statements'][0]['function'];params=copy.deepcopy(fn['parameters']);params[1]['mode']='positional_only'
                updated=client.edit_program(p['id'],p['version'],operations=[{'op':'set','id':fn['id'],'field':'parameters','value':params}])
                self.assertEqual(client.start(p['id'],updated['version'],None,mode='graph').result(),42)
                self.assertEqual(client.analyze(updated['source'])['diagnostics'],[]);self.assertEqual(client.format_source(updated['source']),Framework.format_source(updated['source']))
                self.assertEqual(client.rename(SOURCE,position=SOURCE.index('value:Int'),new_name='private'),Framework.rename(SOURCE,position=SOURCE.index('value:Int'),new_name='private'))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_check_fmt_ir_run_and_actual_stdio_lsp(self):
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
                position=len(SOURCE[:SOURCE.index('value:Int')].encode('utf-16-le'))//2
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(position)}
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),2)
                change=client.request('textDocument/rename',{**params,'newName':'private'});self.assertEqual(len(change['documentChanges'][0]['edits']),2)
                self.assertIn('Int',str(client.request('textDocument/hover',params)))
                self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
