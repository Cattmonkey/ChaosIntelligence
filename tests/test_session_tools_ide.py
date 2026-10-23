"""One static contract for language-bound persistent Session calls."""
import inspect
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Framework, Client, Settings
from chaos.code_presentation import render_code
from chaos.ide_language import analyze, builtin_symbols
from chaos.language import Evaluator, Parser
from chaos.lsp import Job, Server
from chaos.lsp_adapters import convert
from chaos.lsp_workspace import Workspace
from chaos.native_specs import spec_for
from chaos.openapi import specification
from chaos.program import plain
from chaos.server import make_server
from chaos.source_format import format_source
from chaos.suspension import Scheduler

SOURCE='''var seen=[];
fn make(offset:Int){fn(value:Int)->Int{seen=append(seen,value);value+offset}}
agent worker{provider:"model",tools:[]};
let session=await session_open("chat",worker);
let tool=agent_tool("add",make(2),description="Add captured offset",input_type="Int",output_type="Int");
let bound=session_bind(session,[tool]);
let binding=session_binding_info(bound);
let cold=bound(input=40,message_id="one");
let before=seen;
let answer=await cold;
let replay=await bound(40,message_id="one");
[answer.output,before,seen,replay.replayed,binding.session.key,[type_of(tool),type_of(bound),type_of(cold)]]'''
EXPECTED=['42',[],[40],True,'chat',['AgentTool','Function','Task']]
RECEIPT={'key','messageId','status','output','revision','replayed'}


class SessionToolsIDE(unittest.TestCase):
    def result(self,source=SOURCE,**kwargs):
        result=analyze(source,**kwargs)
        self.assertTrue(result['complete'],result['diagnostics']);self.assertEqual(result['diagnostics'],[])
        return result
    def symbols(self,source=SOURCE,**kwargs):
        return {item['name']:item for item in self.result(source,**kwargs)['symbols']}
    def test_native_metadata_matches_actual_runtime_and_function_contract(self):
        runtime=Evaluator().env;symbols=builtin_symbols()
        for name in ('session_bind','session_binding_info','session_info'):
            value=runtime.get(name);spec=spec_for(name);parameters=list(inspect.signature(value.call).parameters.values())
            self.assertEqual(value.spec_id,name);self.assertEqual([p.name for p in parameters],[p.name for p in spec.parameters])
            self.assertEqual([p.default is not p.empty for p in parameters],[p.optional for p in spec.parameters])
            self.assertEqual(symbols[name].builtin,name)
        self.assertEqual(spec_for('session_bind').result_type,'Function')
        symbols=self.symbols()
        self.assertEqual([symbols[n]['type'] for n in ('session','tool','bound','binding','cold','answer')],['AgentSession','AgentTool','Function','Record','Task','Record'])
        self.result(SOURCE+';fn invoke(f:Function){null};invoke(bound)')
    def test_precise_receipt_and_binding_members_have_no_fabricated_handlers(self):
        prefix=SOURCE+';'
        for expression,expected in [('answer.',RECEIPT),('binding.',{'session','tools','bindingHash'}),('binding.session.',{'key','revision','definitionHash'}),('session_info(session).lastToolBinding.',{'kind','definitionHash','tools','bindingHash','runId','sourceVersion'})]:
            self.assertEqual({x['label'] for x in analyze(prefix+expression)['completions']},expected)
        self.assertEqual(analyze(prefix+'bound.')['completions'],[])
        self.assertEqual(self.symbols(prefix+'let output=answer.output')['output']['type'],'String')
    def test_aliases_modules_apply_and_spawn_preserve_native_identity(self):
        module='let bind=session_bind;let value=await session_load("chat");let bound=bind(value,[]);export {bound}'
        source='import "worker.chaos" as Worker;let object={call:Worker.bound};let cold=apply(object.call,[40]);let next=spawn(cold);let result=await next'
        symbols=self.symbols(source,files={'worker.chaos':module})
        self.assertEqual([symbols[n]['type'] for n in ('cold','next','result')],['Task','Task','Record'])
        result=self.result(source,files={'worker.chaos':module},position=source.index('Worker.bound')+len('Worker.bo'))
        self.assertEqual(result['definition']['file'],'worker.chaos')
    def test_shadowed_or_mutable_values_do_not_invent_session_calls(self):
        for source in ['fn session_bind(value,tools){input};let bound=session_bind(input,[]);let cold=bound(1)',
                       'var create=session_bind;let bound=create(input,[]);let cold=bound(1)',
                       'let session=await session_load("chat");var bound=session_bind(session,[]);let cold=bound(1)']:
            self.assertEqual(self.symbols(source)['cold']['type'],'Any')
        for source in ['session_bind(42,[])','session_bind(input,true)','session_binding_info(42)',SOURCE+';bound()',SOURCE+';bound(1,message_id=42)']:
            self.assertTrue(analyze(source)['diagnostics'])
    def test_signature_format_lsp_highlight_are_inert_and_same_ast(self):
        position=SOURCE.index('message_id="one"')+len('message_id=')
        with tempfile.TemporaryDirectory()as folder:
            root=Path(folder);workspace=Workspace([root]);uri=(root/'main.chaos').as_uri()
            workspace.open({'uri':uri,'languageId':'chaos','version':1,'text':SOURCE});snapshot=workspace.snapshot(uri);files,entry=snapshot.load()
            with patch.object(Evaluator,'run',side_effect=AssertionError('analysis ran source')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis resumed task')):
                result=self.result(position=position)
                self.assertEqual(result,Framework.analyze(SOURCE,position=position))
                signature=convert('textDocument/signatureHelp',result,snapshot,files)
                self.assertIn('-> Task',signature['signatures'][0]['label']);self.assertEqual(signature['activeParameter'],1)
                self.assertEqual([p['name'] for p in result['signatureHelp']['parameters']],['input','message_id'])
                formatted=Server(None,None)._calculate(Job(1,'textDocument/formatting',{'options':{'tabSize':2,'insertSpaces':True}},snapshot))[0]['newText']
                self.assertEqual(plain(Parser(SOURCE).program()),plain(Parser(formatted).program()));self.assertEqual(format_source(formatted),formatted)
                self.assertIn('chaos-token-function',render_code(SOURCE,'chaos'))
            self.assertFalse((root/'main.chaos').exists())
    def test_http_sdk_metadata_and_openapi_remain_read_only(self):
        with tempfile.TemporaryDirectory()as folder,Framework(settings=Settings(':memory:'))as app:
            app.create_project('metadata',source='null',capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client('http://127.0.0.1:'+str(server.server_port));position=SOURCE.index('message_id="one"')+len('message_id=')
                self.assertEqual(client.analyze(SOURCE,position=position),Framework.analyze(SOURCE,position=position))
                self.assertEqual(client.format_source(SOURCE),Framework.format_source(SOURCE));self.assertEqual(app.get_project('metadata')['version'],1)
                schemas=client._request('GET','/openapi.json')['components']['schemas']
                self.assertEqual(schemas,specification()['components']['schemas'])
                reference={'$ref':'#/components/schemas/SessionToolBinding'}
                self.assertEqual(schemas['AgentSessionRecord']['properties']['pending']['properties']['toolBinding'],reference)
                self.assertEqual(schemas['AgentSessionHistory']['properties']['items']['items']['properties']['toolBinding'],reference)
                self.assertEqual(schemas['SessionToolBinding']['required'],['kind','definitionHash','tools','bindingHash','runId','sourceVersion'])
                self.assertNotIn('toolBinding',schemas['AgentSessionTurn']['properties']);self.assertFalse(schemas['AgentSessionTurn']['additionalProperties'])
            finally:server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
