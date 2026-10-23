"""Ordered exception patterns and dynamic rethrow share every authoring surface."""
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
SOURCE='// 🙂 ordered errors keep branch scopes separate\nfn add(value:Int)->Int{value+1};let result=try{throw 1}catch {value:value,.._} if value<0{0}catch {code:code,value:value,.._}{add(41)}else{0}finally{null};result'
ORDER_SOURCE='try{throw 1}catch {value:value,.._} if value>0{42}catch fallback{43}'

class ExceptionSurfaces(unittest.TestCase):
    def test_patterns_guards_rethrow_and_historical_ir_roundtrip(self):
        cases=[(SOURCE,42),('try{throw 1}catch _{_.value}',1),('try{throw 1}catch _ if false{0}catch error{error.value}',1),
               ('try{throw 42}catch {code:"other",.._}{0}catch {value:x,.._}{x}',42),
               ('fn again(){throw};try{try{throw 42}catch e{again()}}catch e{e.value}',42),
               ('try{throw}catch {code:"rethrow_error",.._}{42}',42),
               ('try{0}catch {value:x,.._}{x}else{42}finally{null}',42)]
        for source,expected in cases:
            with self.subTest(source=source):
                program=parse_program(source);formatted=format_program(program)
                self.assertEqual(plain(decode_program(program)),plain(Parser(formatted).program()))
                self.assertEqual(Framework.evaluate(source),expected);self.assertEqual(Framework.evaluate(formatted),expected)
        for name in ('error','_','case'):
            node=parse_program('try{}catch '+name+'{}')['root']['statements'][0]
            self.assertEqual(node['kind'],'try');self.assertEqual(set(node)-{'id','kind','span'},{'body','name','handler'})
        self.assertEqual(parse_program('throw')['root']['statements'][0]['kind'],'rethrow')
        self.assertEqual(parse_program('throw null')['root']['statements'][0]['kind'],'throw')
        self.assertIn('try_cases',templates());self.assertIn('rethrow',templates())

    def test_ir_closed_case_validation_and_canonical_single_name(self):
        base=parse_program(ORDER_SOURCE);bad=[]
        for change in [lambda n:n.update(cases=[]),lambda n:n.update(body={'id':'bad_body','kind':'literal','value':1}),lambda n:n['cases'][0].update(pattern={'id':'bad_pattern','kind':'p_any'}),lambda n:n['cases'][0].update(body={'id':'bad_handler','kind':'literal','value':1}),lambda n:n['cases'][0].update(extra=True),lambda n:n.update(cases=[n['cases'][1]])]:
            program=copy.deepcopy(base);change(program['root']['statements'][0]);bad.append(program)
        for program in bad:
            with self.subTest(program=program),self.assertRaises(LanguageError):decode_program(program)
        program=parse_program('try{}catch _ if true{}');self.assertEqual(program['root']['statements'][0]['cases'][0]['pattern']['name'],'_');format_program(program)
        many='try{throw 1}'+''.join('catch {value:'+str(i)+',.._}{'+str(i)+'}' for i in range(65))
        self.assertEqual(len(parse_program(many)['root']['statements'][0]['cases']),65)

    def test_error_shapes_guards_and_no_mismatch_diagnostic(self):
        source='try{throw 1}catch [unused]{0}catch {code:code,message:message,location:where,..rest} if code=="user_throw"{message}'
        result=analyze(source);self.assertEqual(result['diagnostics'],[])
        types={s['name']:s['type']for s in result['symbols']};self.assertEqual(types['code'],'String');self.assertEqual(types['message'],'String');self.assertEqual(types['where'],'Record');self.assertEqual(types['rest'],'Record')
        source='let result=try{"discarded"}catch {value:x,.._}{41}catch e{42}else{43};result'
        self.assertEqual(next(s['type']for s in analyze(source)['symbols']if s['name']=='result'),'Int')
        source='try{0}catch e if 1{42}';self.assertTrue(any(d['code']=='type_mismatch'for d in analyze(source)['diagnostics']))
        source='fn add(value:Int)->Int{value};try{throw 1}catch {value:x,.._} if add('
        self.assertIn('value: Int',analyze(source,position=len(source))['signatureHelp']['label'])
        self.assertEqual(analyze('fn again(){throw};again()')['diagnostics'],[])

    def test_pattern_symbols_are_case_local_and_outer_rename_is_safe(self):
        source='let value=42;try{throw 1}catch {value:value,.._} if value<0{value}catch {value:value,.._}{value}else{value};value'
        first=source.index('value:value')+6;report=references(source,position=first);self.assertTrue(report['complete']);self.assertEqual(len(report['references']),3)
        changed=rename(source,position=first,new_name='negative')['files']['main.chaos'];self.assertIn('value:negative',changed);self.assertIn('catch {value:value',changed);self.assertEqual(Framework.evaluate(changed),42)
        for source,name in [('try{0}catch {value:only,.._}{only}else{only}','only'),('try{0}catch e if true{e}catch other{e}','e')]:
            self.assertTrue(any(d['code']=='undefined_name'and name in d['message']for d in analyze(source)['diagnostics']))
        source='try{throw 1}catch _ if false{_}catch e{e.value}'
        changed=rename(source,position=source.index('_'),new_name='original')['files']['main.chaos'];self.assertEqual(Framework.evaluate(changed),1)

    def test_formatter_preserves_bare_throw_delimiters_comments_and_else(self):
        cases=[SOURCE,'fn again(){throw // keep original\n};try{throw 42}catch {value:x,.._}{try{again()}catch e{e.value}}',
               'try{throw 1}// first\ncatch {code:"none",.._}{0}// next\ncatch error if true{error.value}else{0}finally{null}',
               'try{try{throw 1}catch {value:x,.._}{throw;}}catch e{e.value}',
               'try{throw 1}catch {value:x,.._}{if true{throw}else{0}}',
               'fn again(){return (throw)};try{try{throw 42}catch e{again()}}catch e{e.value}']
        for source in cases:
            for width in (40,88):
                with self.subTest(source=source,width=width):
                    formatted=format_source(source,line_width=width);self.assertEqual(format_source(formatted,line_width=width),formatted)
                    self.assertEqual(plain(Parser(source).program()),plain(Parser(formatted).program()))
        self.assertIn('// first',format_source(cases[2]))

    def test_rethrow_ir_parentheses_preserve_expression_binding(self):
        sources=['let value=(throw)+1;value','1+(throw)','(throw)(1)',
                 '(throw).code','[throw]','{value:(throw)}','!(throw)',
                 'fn f(){return (throw)}','fn* f(){yield (throw)}',
                 'try{0}catch e if (throw){}','while (throw){}']
        for version in (1,2):
            for source in sources:
                with self.subTest(source=source,version=version):
                    program=parse_program(source,language_version=version)
                    formatted=format_program(program)
                    self.assertEqual(plain(decode_program(program)),plain(Parser(formatted).program()))
                    self.assertEqual(format_source(format_source(source,language_version=version)),format_source(source,language_version=version))
        source='try{try{throw 42}catch e{(throw)+1}}catch outer{outer.value}'
        self.assertEqual(Framework.evaluate(format_program(parse_program(source))),42)

    def test_analysis_and_symbols_do_not_execute_guards_or_rethrow(self):
        source='try{throw 1}catch {code:code,.._} if tool("never",code){throw}catch other{throw}'
        with patch.object(Evaluator,'run',side_effect=AssertionError('ran')),patch.object(Evaluator,'evaluate',side_effect=AssertionError('evaluated')):
            self.assertEqual(analyze(source)['diagnostics'],[]);format_source(source);self.assertTrue(references(source,position=source.index('code:code')+5)['complete'])

    def test_http_sdk_case_reorder_run_snapshot_and_format(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('exception-surfaces',source=ORDER_SOURCE,capabilities=[])
                old=client.start(project['id'],project['version'],None);self.assertEqual(old.result(),42)
                program=client.program(project['id'])['program'];node=program['root']['statements'][0]
                updated=client.edit_program(project['id'],project['version'],operations=[{'op':'set','id':node['id'],'field':'cases','value':list(reversed(node['cases']))}])
                self.assertEqual(client.start(project['id'],updated['version'],None,mode='graph').result(),43);self.assertEqual(old.get()['snapshot']['source'],ORDER_SOURCE)
                self.assertEqual(client.analyze(updated['source'])['diagnostics'],[]);self.assertEqual(client.format_source(updated['source']),format_source(updated['source']))
            finally:server.shutdown();thread.join(3);server.server_close()

    def test_cli_lsp_pattern_reference_rename_hover_and_tokens(self):
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
                position=len(SOURCE[:SOURCE.index('code:code')+5].encode('utf-16-le'))//2
                params={'textDocument':{'uri':path.as_uri()},'position':TextMap(SOURCE).position(position)}
                refs=client.request('textDocument/references',{**params,'context':{'includeDeclaration':True}});self.assertEqual(len(refs),1)
                change=client.request('textDocument/rename',{**params,'newName':'error_code'});self.assertEqual(len(change['documentChanges'][0]['edits']),1)
                self.assertIn('String',str(client.request('textDocument/hover',params)));self.assertTrue(client.request('textDocument/semanticTokens/full',{'textDocument':{'uri':path.as_uri()}})['data']);self.assertEqual(path.read_text(),SOURCE)
            finally:client.close()
