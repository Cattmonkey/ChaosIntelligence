"""Actual callable metadata for explicitly bound Team roles, without execution."""
import inspect
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from chaos import Client,Framework,Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze,builtin_symbols
from chaos.language import Evaluator,Parser
from chaos.lsp import Job,Server
from chaos.lsp_adapters import convert
from chaos.lsp_workspace import Workspace
from chaos.native_specs import spec_for
from chaos.program import plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.suspension import Scheduler

DEFINITION='{kind:"team",name:"Function crew",entry:"worker",roles:[{id:"worker",parent:null,capabilities:[],agent:{kind:"agent",name:"worker",config:{}}},{id:"fallback",parent:"worker",capabilities:[],agent:{kind:"agent",name:"fallback",config:{}}}]}'
MODULE='let crew=team_from('+DEFINITION+');export {crew}'
SOURCE='''import "teams/crew.chaos" as Team;
var seen=[];
let offset=1;
fn work(left:Int,right:Int=0,*,scale:Int=1)->Int{seen=append(seen,left);(left+right+offset)*scale}
let bound=team_bind(Team.crew,{worker:work});
let roles=team_bindings(bound);
let info=team_binding_info(bound);
let cold=bound(20,right=0,scale=2);
let before=seen;
let answer=await cold;
let second=await roles.worker(10,right=2,scale=3);
[answer,second,before,seen,info.bindings.worker.kind,[type_of(bound),type_of(cold)]]'''
EXPECTED=[42,39,[],[20,10],'Function',['Function','Task']]
FILES={'teams/crew.chaos':MODULE}

class TeamFunctionsIDE(unittest.TestCase):
    def result(self,source=SOURCE,**kw):
        result=analyze(source,files=kw.pop('files',FILES),**kw)
        self.assertTrue(result['complete'],result['diagnostics']);self.assertEqual(result['diagnostics'],[])
        return result
    def symbols(self,source=SOURCE,**kw):return {x['name']:x for x in self.result(source,**kw)['symbols']}
    def test_native_metadata_exact_runtime_parameters(self):
        env=Evaluator().env;symbols=builtin_symbols()
        for name in ('team_from','team_info','team_source','team_bind','team_bindings','team_role','team_binding_info'):
            native=env.get(name);spec=spec_for(name);params=list(inspect.signature(native.call).parameters.values())
            self.assertEqual(native.spec_id,name);self.assertEqual([x.name for x in params],[x.name for x in spec.parameters])
            self.assertEqual([x.default is not x.empty for x in params],[x.optional for x in spec.parameters]);self.assertEqual(symbols[name].builtin,name)
        values=self.symbols();self.assertEqual([values[n]['type']for n in ('bound','roles','info','cold','answer','second')],['Function','Record','Record','Task','Int','Int'])
    def test_binding_members_fallback_and_override_metadata(self):
        for expr,names in [('roles.',{'worker','fallback'}),('info.',{'team','definitionHash','bindings'}),('info.team.',{'kind','name','entry','roles'}),('info.bindings.',{'worker'}),('info.bindings.worker.',{'kind'})]:
            self.assertEqual({x['label']for x in analyze(SOURCE+';'+expr,files=FILES)['completions']},names)
        self.assertEqual(analyze(SOURCE+';bound.',files=FILES)['completions'],[])
        symbols=self.symbols(SOURCE+';let raw=team_bindings(Team.crew);let cold2=raw.worker(1);let fallback=await roles.fallback(1)')
        self.assertEqual(symbols['cold2']['type'],'Task');self.assertEqual(symbols['fallback']['type'],'Any')
    def test_real_parameter_modes_and_diagnostics(self):
        text=SOURCE+';roles.worker(20,right=3,scale=2)';position=text.rindex('scale=')+6
        result=self.result(text,position=position);help=result['signatureHelp']
        self.assertEqual([p['name']for p in help['parameters']],['left','right','scale']);self.assertEqual(help['activeParameter'],2)
        self.assertIn('*, scale: Int',help['label']);self.assertIn('-> Task',help['label'])
        for expression in ['bound()','bound("bad")','roles.worker(1,2,3)','roles.worker(1,missing=2)','roles.worker(1,scale="bad")','roles.fallback()']:
            self.assertTrue(analyze(SOURCE+';'+expression,files=FILES)['diagnostics'],expression)
    def test_async_one_layer_and_session_receipt(self):
        prefix='let raw=team_from('+DEFINITION+');'
        symbols=self.symbols(prefix+'async fn number(value:Int)->Int{value};let bound=team_bind(raw,{worker:number});let answer=await bound(2)')
        self.assertEqual(symbols['answer']['type'],'Int')
        symbols=self.symbols(prefix+'async fn nested(value:Int)->Task{sleep(value)};let bound=team_bind(raw,{worker:nested});let answer=await bound(2)')
        self.assertEqual(symbols['answer']['type'],'Task')
        source=prefix+'let session=await session_load("chat");let live=session_bind(session,[]);let bound=team_bind(raw,{worker:live});let role=team_role(bound,"worker");let answer=await role(40,message_id="one")'
        self.assertEqual(self.symbols(source)['answer']['type'],'Record')
        self.assertEqual({x['label']for x in analyze(source+';answer.')['completions']},{'key','messageId','status','output','revision','replayed'})
        help=self.result(source,position=source.index('message_id=')+11)['signatureHelp'];self.assertEqual([p['name']for p in help['parameters']],['input','message_id'])
    def test_aliases_and_dynamic_unknowns_do_not_guess_names(self):
        source=SOURCE.replace('team_bind(Team.crew','alias(Team.crew').replace('let bound=','let alias=team_bind;let bound=')
        self.assertEqual(self.symbols(source)['answer']['type'],'Int')
        source='import "module.chaos" as M;let answer=await M.bound(20,scale=2)'
        module=MODULE.replace('export {crew}','fn work(left:Int,*,scale:Int=1)->Int{left*scale};let bound=team_bind(crew,{worker:work});export {bound}')
        self.assertEqual(self.symbols(source,files={'module.chaos':module})['answer']['type'],'Int')
        for text in ['fn team_bind(value,bindings){input};let bound=team_bind(input,{});let answer=bound(1)',
                     'var make=team_bind;let bound=make(input,{});let answer=bound(1)']:
            self.assertEqual(self.symbols(text)['answer']['type'],'Any')
        text='fn work(x:Int)->Int{x};let bound=team_bind(team_from(input),{worker:work});let answer=await team_role(bound,"worker")(1)'
        self.assertEqual(self.symbols(text)['answer']['type'],'Int')
        text='let raw=team_from('+DEFINITION+');let bound=team_bind(raw,input);let answer=await team_role(bound,"worker")(1,extra=2)'
        self.assertEqual(self.symbols(text)['answer']['type'],'Any')
    def test_callable_instance_partial_zero_arguments_and_unknown_spreads(self):
        prefix='let raw=team_from('+DEFINITION+');'
        for implementation,setup,call,expected in [
            ('Worker()','class Worker{fn __call__(self,value:Int,*,delta:Int=1)->Int{value+delta}}','(2,delta=3)','Int'),
            ('partial(add,2)','fn add(a:Int,b:Int)->Int{a+b}','(3)','Any'),
            ('zero','fn zero()->Int{42}','()','Int')]:
            source=prefix+setup+';let bound=team_bind(raw,{worker:'+implementation+'});let answer=await bound'+call
            self.assertEqual(self.symbols(source)['answer']['type'],expected)
        source=prefix+'fn work(value:Int)->Int{value};let args=[raw,{worker:work}];let bound=team_bind(*args);let answer=await bound(1)'
        self.assertEqual(self.symbols(source)['answer']['type'],'Any')

    def test_source_format_lsp_highlight_and_readonly_http_parity(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            path=Path(folder).resolve();workspace=Workspace([path]);uri=(path/'main.chaos').as_uri()
            for name,text in FILES.items():
                target=path/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':SOURCE});snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            position=SOURCE.index('right=0')+6
            with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis resumed')):
                result=self.result(position=position)
                signature=convert('textDocument/signatureHelp',result,snapshot,files);self.assertEqual(signature['activeParameter'],1)
                formatted=Server(None,None)._calculate(Job(1,'textDocument/formatting',{'options':{'tabSize':2,'insertSpaces':True}},snapshot))[0]['newText']
                self.assertEqual(plain(Parser(formatted).program()),plain(Parser(SOURCE).program()));self.assertEqual(format_source(formatted),formatted)
                self.assertIn('chaos-token-function',render_code(SOURCE,'chaos'))
            app.create_project('pure',source='null',capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=path/'control',update_home=path/'updates');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port))
                self.assertEqual(client.analyze(SOURCE,files=FILES,position=position),Framework.analyze(SOURCE,files=FILES,position=position))
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE));self.assertEqual(app.get_project('pure')['version'],1)
            finally:server.shutdown();thread.join(3);server.server_close()
