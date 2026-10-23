"""Exact ordinary message outcomes through real language and public transports."""
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from chaos import Client, Final, Framework, Registry, ProviderSpec, Settings, ToolCall
from chaos.client import APIError, NotFoundError, ValidationError
from chaos.ide_language import analyze
from chaos.language import Evaluator, LanguageError, Native, Parser
from chaos.modules import STDLIB, declarations
from chaos.native_specs import SHAPES, all_specs, spec_for
from chaos.program import format_program, parse_program
from chaos.server import make_server
from chaos.suspension import Scheduler
from tests.test_process_integration import definition, wait_for


FIELDS = {'key','messageId','mailboxStatus','linkStatus','run','reconciliation'}
RUN_FIELDS = {'id','version','status','output','error','created','updated'}


def populate(app, project='p', *, registry=None):
    app._engine.pause_dispatch(True)
    source = 'fn step(c){process_step(c.state+1,c.input)};export{step}'
    app.create_project(project, source='null', files={'dot.chaos': source}, capabilities=['process.manage'])
    app.create_process(project,1,'worker',definition(maxTurns=3),state=0)
    app.send_process(project,'worker',21,message_id='first')
    app.send_process(project,'worker',42,message_id='second')
    app._engine.pause_dispatch(False)
    final=wait_for(app,project,'worker',lambda row:row['status']=='completed',timeout=6)
    assert final['output']==42
    return final


@contextmanager
def outcome_service():
    with tempfile.TemporaryDirectory() as folder, Framework(settings=Settings(':memory:')) as app:
        populate(app)
        app._engine.pause_dispatch(True)
        app.create_process('p',1,'other',definition(maxTurns=2),state=100)
        app.send_process('p','other',99,message_id='first')
        app._engine.pause_dispatch(False)
        wait_for(app,'p','other',lambda row:row['status']=='completed',timeout=6)
        app.create_project('q',source='null',capabilities=[])
        server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        try:yield app,Client(origin),origin
        finally:server.shutdown();thread.join(3);server.server_close()


class ProcessMessageSurfaces(unittest.TestCase):
    def test_native_constructor_is_cold_strict_and_never_generates_an_identity(self):
        calls=[]
        host=lambda op,payload:calls.append((op,payload)) or {'key':'worker','messageId':'first'}
        with patch('chaos.process_store.uuid.uuid4',side_effect=AssertionError('lookup generated identity')):
            self.assertEqual(Evaluator(process_handler=host).run('type_of(process_message("worker","first"))'),'Task')
            for expression in ['process_message("worker",null)','process_message("worker",true)',
                               'process_message("worker",42)','process_message("worker","")',
                               'process_message("worker","bad/id")','process_message("worker","'+('a'*129)+'")',
                               'process_message("bad key","first")','process_message("worker")']:
                with self.subTest(expression=expression),self.assertRaises(LanguageError):
                    Evaluator(process_handler=host).run(expression)
        self.assertEqual(calls,[])
        result=Evaluator(process_handler=host).run('await process_message(key="worker",message_id="first")')
        self.assertEqual(result,{'key':'worker','messageId':'first'})
        self.assertEqual(calls,[('message',{'key':'worker','messageId':'first'})])
        with self.assertRaises(LanguageError):Evaluator().run('await process_message("worker","first")')

    def test_single_native_contract_aliases_and_nested_completion_are_inert(self):
        env=Evaluator().env.values;native=env['process_message'];spec=spec_for('process_message')
        self.assertEqual([p.name for p in inspect.signature(native.call).parameters.values()],['key','message_id'])
        self.assertEqual([p.name for p in spec.parameters],['key','message_id'])
        self.assertEqual((spec.result_type,spec.awaited_type,spec.awaited_shape),('Task','Record','ProcessMessageOutcome'))
        self.assertEqual({p.name for p in SHAPES['ProcessMessageOutcome']},FIELDS)
        self.assertEqual({p.name for p in SHAPES['ProcessMessageRun']},RUN_FIELDS)
        self.assertEqual(len([v for v in env.values()if type(v)is Native]),335)
        self.assertEqual(len(all_specs()),381);self.assertEqual(len(STDLIB),21)
        process_names={'process_from','process_info','process_source','process_start','process_send','process_guide','process_message','process_status','process_events','process_control','process_configure','process_reconcile','process_step','process_evolve','process_evolution_info'}
        self.assertEqual(set(declarations(Parser(STDLIB['std/process']).program())),process_names)
        self.assertEqual(Framework.evaluate('import "std/process" as P;['+','.join('P.'+name+'=='+name for name in sorted(process_names))+']'),[True]*len(process_names))
        self.assertEqual(set(declarations(Parser(STDLIB['std/bot']).program())),{'bot','bot_info','bot_context','bot_reconcile','process_guide','process_events','process_message'})
        self.assertEqual(Framework.evaluate('import "std/process" as P;import "std/bot" as B;[P.process_message==process_message,B.process_message==process_message,type_of(P.process_message("worker","first"))]'),[True,True,'Task'])
        source='import "std/process" as P;let query=P.process_message;let pending=query("worker","first");let result=await pending;'
        with patch.object(Evaluator,'run',side_effect=AssertionError('analysis executed')),patch.object(Scheduler,'_s_resume',side_effect=AssertionError('analysis resumed')):
            report=analyze(source+'result.')
            self.assertEqual({c['label']for c in report['completions']},FIELDS)
            self.assertEqual({c['label']for c in analyze(source+'result.run.')['completions']},RUN_FIELDS)
        self.assertEqual({s['name']:s['type']for s in report['symbols']}['pending'],'Task')
        self.assertEqual(Framework.evaluate('fn process_message(key,message_id)->Int{7};process_message("worker","first")'),7)

    def test_original_message_output_is_not_latest_process_output_and_resend_is_readonly(self):
        with Framework(settings=Settings(':memory:')) as app:
            final=populate(app);before=app.events('p',limit=300)['lastEventId']
            first=app.get_process_message('p','worker','first');second=app.get_process_message('p','worker','second')
            self.assertEqual(set(first),FIELDS);self.assertEqual(first['linkStatus'],'linked')
            self.assertEqual(first['mailboxStatus'],'consumed');self.assertEqual(set(first['run']),RUN_FIELDS)
            self.assertEqual(first['run']['output'],{'state':2,'output':21,'done':False})
            self.assertEqual(second['run']['output'],{'state':3,'output':42,'done':False})
            self.assertNotEqual(first['run']['id'],second['run']['id']);self.assertEqual(final['output'],42)
            self.assertIsNone(first['reconciliation']);self.assertEqual(first['run']['status'],'completed')
            self.assertEqual(app.send_process('p','worker',21,message_id='first')['status'],'consumed')
            self.assertEqual(app.get_process_message('p','worker','first'),first)
            self.assertEqual(app.events('p',limit=300)['lastEventId'],before)

    def test_actual_source_graph_code_program_agenttool_and_bot_share_the_same_read(self):
        registry=Registry()
        registry.register_provider(ProviderSpec('pick',lambda request,context:
            Final(request.history[-1]['output'])if request.history else ToolCall('lookup',request.input)))
        with Framework(settings=Settings(':memory:'),registry=registry) as app:
            populate(app)
            simple='await process_message("worker","first")'
            function='fn(id){process_message("worker",id)}'
            program={'kind':'program','entry':'main.chaos','files':{'main.chaos':simple},'languageVersions':{'main.chaos':2}}
            sources=[simple,'let lookup=function_from(code_from('+json.dumps(function)+'));await lookup("first")',
                     'let code=program_from('+json.dumps(program)+');await code(null)',
                     'agent a{provider:"pick",tools:[]};let bound=agent_bind(a,[agent_tool("lookup",fn(id){process_message("worker",id)})]);await bound("first")',
                     'let b=bot("query",fn(s){"first"},async fn(id,s){await process_message("worker",id)},fn(s){{done:true,context:null,output:s.evidence[0]}});let result=await b();result.output']
            expected=app.get_process_message('p','worker','first')
            for index,source in enumerate(sources):
                parsed=parse_program(source);formatted=format_program(parsed)
                self.assertEqual(parse_program(formatted)['semanticHash'],parsed['semanticHash'])
                current=app.get_project('p');project=app.edit_program('p',current['version'],source=formatted)
                for mode in ('source','graph'):
                    with self.subTest(index=index,mode=mode):
                        self.assertEqual(app.start('p',project['version'],None,mode=mode).result(8),expected)

    def test_http_sdk_cli_and_openapi_read_exact_message_and_reject_other_namespaces(self):
        with outcome_service() as (app,client,origin):
            first=client.get_process_message('p','worker','first')
            self.assertEqual(first,app.get_process_message('p','worker','first'))
            self.assertEqual(first['run']['output']['output'],21)
            command=[sys.executable,'-m','chaos','process','message','p','worker','--message-id','first','--server',origin]
            cli=subprocess.run(command,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=8)
            self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),first)
            missing=subprocess.run(command[:7]+command[9:],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=8)
            self.assertNotEqual(missing.returncode,0)
            for project,key,ident in [('q','worker','first'),('p','worker','missing')]:
                with self.subTest(project=project,key=key),self.assertRaises(NotFoundError):client.get_process_message(project,key,ident)
            app._engine.pause_dispatch(True)
            app.create_process('p',1,'guidance-only',definition(),state=0)
            app.guide_process('p','guidance-only',{'text':'guidance is not ordinary mail'},message_id='first')
            with self.assertRaises(NotFoundError):client.get_process_message('p','guidance-only','first')
            with self.assertRaises(ValidationError):client._request('GET','/projects/p/processes/worker/messages/first',query={'latest':'true'})
            with self.assertRaises(APIError) as wrong_method:client._request('POST','/projects/p/processes/worker/messages/first',{})
            self.assertEqual(wrong_method.exception.status,405)
            schemas=client._request('GET','/openapi.json')['components']['schemas']
            self.assertEqual(set(schemas['ProcessMessageOutcome']['required']),FIELDS)
            self.assertFalse(schemas['ProcessMessageOutcome']['additionalProperties'])
            self.assertEqual(set(schemas['ProcessMessageRun']['required']),RUN_FIELDS)
            self.assertEqual(schemas['ProcessMessageReconciliation']['properties']['evidence']['type'],['string','null'])

    def test_recording_rejects_the_read_without_observing_host(self):
        reads=[];operations=[]
        def recording(operation,payload):
            operations.append(operation)
            if operation=='recording.begin':return {'scopeId':'scope'}
            if operation=='recording.abort':return None
            if operation=='recording.finish':raise LanguageError('recording poisoned')
            raise AssertionError(operation)
        with self.assertRaises(LanguageError):
            Evaluator(mode_handler=recording,process_handler=lambda *a:reads.append(a)).run('async fn action(x){try{await process_message("worker","first")}catch e{null};42};await record_skill(action)')
        self.assertIn('recording.abort',operations);self.assertEqual(reads,[])


if __name__=='__main__':unittest.main()
