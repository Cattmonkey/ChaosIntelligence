"""Per-turn language tool contracts, real AST callbacks and durable Sessions."""
import copy
import json
import importlib.util
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from chaos import Framework,Registry,ToolSpec
from chaos.settings import Settings
from chaos.language import LanguageError
from chaos.session_protocol import tool_alias
from chaos.store import Conflict
from tests.test_harness_runtime import FakeClient,message,call


def response(name='twice',value=21,ident='call'):
    return message(calls=[call(tool_alias(name),json.dumps({'input':value}),ident)])


def source(body='[await b("hello",message_id="one"),seen]',handler='fn(x){seen=append(seen,x);x*2}',tools='[]',caps='[]'):
    return ('agent a{provider:"model",tools:'+tools+'};let s=await session_open("chat",a);var seen=[];'
            'let b=session_bind(s,[agent_tool("twice",'+handler+',capabilities='+caps+')]);'+body)


class SessionToolTests(unittest.TestCase):
    def app(self,responses,**kwargs):
        settings=Settings(':memory:');settings.update({'providers':{'text':{'baseURL':'https://fixture.invalid/v1','apiKey':'fixture-only'}}},1)
        client=FakeClient(responses);guard=patch.object(settings,'client',return_value=client);guard.start();self.addCleanup(guard.stop)
        app=Framework(settings=settings,**kwargs);self.addCleanup(app.close)
        return app,client

    def run_source(self,app,code,caps=None):
        app.create_project('p',source=code,capabilities=caps or ['session.manage'])
        return app.start('p',1,None,mode='source').result(5)

    def test_original_closure_runs_and_protocol_and_contract_are_persisted(self):
        app,client=self.app([response(),message('done')]);receipt,seen=self.run_source(app,source())
        self.assertEqual(seen,[21]);self.assertEqual(receipt['output'],'done');self.assertTrue(client.closed)
        row=app.agent_session('p','chat');binding=row['lastToolBinding']
        self.assertEqual(set(binding),{'kind','definitionHash','tools','bindingHash','runId','sourceVersion'})
        history=app.agent_session_history('p','chat')['items'];self.assertEqual(history[2]['message']['content'],42)
        self.assertTrue(all(item['toolBinding']==binding for item in history));self.assertEqual(binding['sourceVersion'],1)

    def test_cold_binding_metadata_and_function_contract(self):
        app,client=self.app([])
        result=self.run_source(app,source('let f:Function=b;[type_of(b),type_of(f(1)),session_binding_info(b),type_of(parse_json(json_stringify(b)))]'))
        self.assertEqual(result[:2],['Function','Task']);self.assertEqual(result[-1],'Record');self.assertEqual(client.requests,[])
        self.assertEqual(set(result[2]),{'session','tools','bindingHash'});self.assertNotIn('lastToolBinding',app.agent_session('p','chat'))

    def test_async_handler_waits_exactly_one_task_and_rejects_handles(self):
        for handler,success in [('async fn(x){await sleep(0);x*2}',True),('async fn(x){async fn later(){42};later()}',False),('fn(x){fn(y){y}}',False)]:
            with self.subTest(handler=handler):
                app,client=self.app([response(),message('done')])
                if success:self.assertEqual(self.run_source(app,source(handler=handler))[0]['output'],'done')
                else:
                    with self.assertRaises(Exception):self.run_source(app,source(handler=handler))
                    self.assertEqual(app.agent_session('p','chat')['status'],'needs_reconciliation')

    def test_same_id_replays_before_revision_without_running_new_handler(self):
        app,client=self.app([response(),message('done')])
        body='let old=await session_load("chat");let one=await b(1,"one");let again=session_bind(old,[agent_tool("twice",fn(x){throw "must not execute"})]);[one,await again(1,"one"),seen]'
        result=self.run_source(app,source(body));self.assertFalse(result[0]['replayed']);self.assertTrue(result[1]['replayed'])
        self.assertEqual(result[2],[21]);self.assertEqual(len(client.requests),2)

    def test_changed_contract_same_id_conflicts_without_another_model_call(self):
        app,client=self.app([response(),message('done')])
        body='await b(1,"one");let changed=session_bind(s,[agent_tool("twice",fn(x){x*2},description="different")]);try{await changed(1,"one")}catch e{e.code}'
        self.assertEqual(self.run_source(app,source(body)),'effect_error');self.assertEqual(len(client.requests),2)

    def test_bound_empty_and_unbound_are_distinct_in_both_directions(self):
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                app,client=self.app([message('first')]);first,second=('b','s')if reverse else('s','b')
                code='agent a{provider:"model",tools:[]};let s=await session_open("chat",a);let b=session_bind(s,[]);await '+first+'(1,"same");try{await '+second+'(1,"same")}catch e{e.code}'
                self.assertEqual(self.run_source(app,code),'effect_error');self.assertEqual(len(client.requests),1)
                row=app.agent_session('p','chat')
                with self.assertRaises(Conflict):
                    app._store.sessions.preflight('p','chat',row['revision'],1,'same',['session.manage'],tool_binding=None if reverse else __import__('chaos.session_tools',fromlist=['contract_binding']).contract_binding(row['definitionHash'],[]))

    def test_canonical_tool_order_has_same_identity(self):
        app,client=self.app([])
        code='agent a{provider:"model",tools:[]};let s=await session_open("chat",a);let first=agent_tool("a",fn(x){x});let z=agent_tool("z",fn(x){x});[session_binding_info(session_bind(s,[z,first])),session_binding_info(session_bind(s,[first,z]))]'
        one,two=self.run_source(app,code);self.assertEqual(one,two)

    def test_omitted_message_id_is_a_valid_identifier_for_plain_and_bound_calls(self):
        import uuid
        from chaos.modes import identifier
        for bound in (False,True):
            with self.subTest(bound=bound):
                app,client=self.app([message('done')]);original=app._store.sessions.claim
                def claim(*args,**kwargs):
                    with patch('chaos.session_store.uuid.uuid4',return_value=uuid.UUID(int=1)):
                        return original(*args,**kwargs)
                code='agent a{provider:"model",tools:[]};let s=await session_open("chat",a);let b=session_bind(s,[]);await '+('b'if bound else's')+'(1)'
                with patch.object(app._store.sessions,'claim',side_effect=claim):receipt=self.run_source(app,code)
                self.assertEqual(identifier(receipt['messageId']),receipt['messageId']);self.assertEqual(receipt['output'],'done')

    def test_historical_numeric_uuid_message_id_replays_through_language_and_sdk(self):
        legacy='0123456789abcdef0123456789abcdef'
        for bound in (False,True):
            with self.subTest(bound=bound):
                app,client=self.app([message('done')])
                prefix='agent a{provider:"model",tools:[]};let s=await session_open("chat",a);let b=session_bind(s,[]);'
                self.run_source(app,prefix+'await '+('b'if bound else's')+'(1,"original")')
                with app._store.write():app._store.db.execute('UPDATE session_turns SET message_id=? WHERE message_id=?',(legacy,'original'))
                if not bound:
                    row=app.agent_session('p','chat')
                    self.assertTrue(app.start_session_turn('p',1,'chat',row['revision'],1,message_id=legacy).result(5)['replayed'])
                project=app.edit_program('p',1,source=prefix+'await '+('b'if bound else's')+'(1,"'+legacy+'")')
                receipt=app.start('p',project['version'],None,mode='source').result(5)
                self.assertTrue(receipt['replayed']);self.assertEqual(receipt['messageId'],legacy);self.assertEqual(len(client.requests),1)

    def test_host_name_collision_and_capability_expansion_are_rejected_before_claim(self):
        app,client=self.app([])
        code='agent a{provider:"model",tools:["text.normalize"]};let s=await session_open("chat",a);[try{session_bind(s,[agent_tool("text.normalize",fn(x){x})])}catch e{e.code},try{session_bind(s,[agent_tool("x",fn(x){x},capabilities=["memory.get"])])}catch e{e.code}]'
        self.assertEqual(self.run_source(app,code,['session.manage','text.normalize']),['argument_error','capability_error'])
        self.assertEqual(app.agent_session('p','chat')['turn'],0);self.assertEqual(client.requests,[])

    def test_handler_capabilities_remain_narrow(self):
        seen=[];registry=Registry();registry.register_tool(ToolSpec('test.secret',lambda x,c:seen.append(x) or 42))
        app,client=self.app([response(),message('done')],registry=registry)
        with self.assertRaises(Exception):self.run_source(app,source(handler='fn(x){tool("test.secret",x)}',tools='["test.secret"]'),['session.manage','test.secret'])
        self.assertEqual(seen,[]);self.assertEqual(app.agent_session('p','chat')['status'],'needs_reconciliation')

    def test_escaped_binding_and_its_cold_task_keep_creation_authority(self):
        for deferred in (False,True):
            with self.subTest(deferred=deferred):
                app,client=self.app([response(),message('done')])
                handler='fn(x){seen=session_bind(s,[]);'+('seen=seen(1,"escaped");'if deferred else'')+'0}'
                body='await b(1,"outer");try{await '+('seen'if deferred else'seen(1,"escaped")')+';"leaked"}catch e{e.code}'
                self.assertNotEqual(self.run_source(app,source(body,handler=handler)),'leaked')
                self.assertEqual(app.agent_session('p','chat')['turn'],1);self.assertEqual(len(client.requests),2)

    def test_bad_full_native_batch_runs_zero_callbacks(self):
        app,client=self.app([message(calls=[call(tool_alias('twice'),'{}','good'),call('unknown','{}','bad')])])
        with self.assertRaises(Exception):self.run_source(app,source())
        history=app.agent_session_history('p','chat')['items'];self.assertEqual([item['message']['role']for item in history],['user'])
        self.assertTrue(client.closed)

    def test_fork_copies_binding_provenance_and_empty_fork_omits_last_binding(self):
        app,client=self.app([response(),message('done')]);self.run_source(app,source())
        row=app.agent_session('p','chat');fork=app.fork_agent_session('p','chat',row['revision'],'copied')
        self.assertEqual(fork['lastToolBinding'],row['lastToolBinding'])
        self.assertEqual(app.agent_session_history('p','copied')['items'][0]['toolBinding'],row['lastToolBinding'])
        compact=app.compact_agent_session('p','chat',row['revision'],0)
        empty=app.fork_agent_session('p','chat',compact['revision'],'empty');self.assertNotIn('lastToolBinding',empty)

    def test_plain_turn_after_bound_retains_latest_bound_receipt(self):
        app,client=self.app([response(),message('bound'),message('plain')]);self.run_source(app,source('await b(1,"one");let before=session_info(s).lastToolBinding;await s(2,"two");[before,session_info(s).lastToolBinding]'))
        history=app.agent_session_history('p','chat')['items'];self.assertNotIn('toolBinding',history[-1])
        self.assertEqual(app.agent_session('p','chat')['lastToolBinding']['sourceVersion'],1)

    def test_journal_failure_prevents_callback_and_requires_reconciliation(self):
        app,client=self.app([response(),message('done')]);journal=app._store.sessions.journal
        def fail(claim,messages,redact):
            if messages[0]['role']=='assistant':raise RuntimeError('journal unavailable')
            return journal(claim,messages,redact)
        with patch.object(app._store.sessions,'journal',fail),self.assertRaises(Exception):self.run_source(app,source())
        self.assertEqual(app.agent_session('p','chat')['status'],'needs_reconciliation');self.assertTrue(client.closed)

    @unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
    def test_actual_browser_continuity_and_explicit_callback_resource_split(self):
        from chaos.execution import BrowserBroker,BrowserSession
        instances=[]
        class Page(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                raw=('<html><title>'+self.path[1:]+'</title><body>fixture</body></html>').encode()
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=ThreadingHTTPServer(('127.0.0.1',0),Page)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join,2);self.addCleanup(server.shutdown)
        origin='http://127.0.0.1:'+str(server.server_port)
        class ObservedBrowser(BrowserSession):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);self.closed_at=None;self.created_at=threading.get_ident();instances.append(self)
            def close(self):
                super().close();self.closed_at=threading.get_ident()
        registry=Registry();registry.register_tool(ToolSpec('test.browser_state',lambda x,c:[b.closed_at is not None for b in instances]))
        app,client=self.app([response('browser.control',{'action':'navigate','url':origin+'/native'}),
                            response(),response('browser.control',{'action':'snapshot'}),message('done')],registry=registry)
        handler='fn(x){let page=tool("browser.control",{action:"navigate",url:'+json.dumps(origin+'/caller')+'});page.title}'
        code=source('await b(1);tool("test.browser_state",null)',handler=handler,tools='["browser.control"]',caps='["browser.control"]')
        with patch('chaos.execution.BrowserBroker',side_effect=lambda *args,**kwargs:BrowserBroker(*args,session_factory=ObservedBrowser,**kwargs)):
            state=self.run_source(app,code,['session.manage','browser.control','test.browser_state'])
        self.assertEqual(state,[True,False]);self.assertEqual(len(instances),2)
        self.assertTrue(all(b.closed_at==b.created_at for b in instances))
        outputs=[item['message']['content'] for item in app.agent_session_history('p','chat')['items']if item['message']['role']=='tool']
        self.assertEqual(outputs[0]['title'],'native');self.assertEqual(outputs[1],'caller');self.assertEqual(outputs[2]['title'],'native')

    def test_same_handle_reentrancy_is_rejected(self):
        app,client=self.app([response(),message('done')])
        code=source('try{await gather([b(1),b(2)])}catch e{e.code}')
        self.assertEqual(self.run_source(app,code),'session_busy');self.assertTrue(client.closed or not client.requests)


if __name__=='__main__':unittest.main()
