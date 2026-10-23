"""Contexts use actual Registry effects and optional local Chromium resources.

Agent providers below are deterministic local Registry handlers, not paid model
calls. Browser tests use the real host path against a loopback HTTP fixture.
"""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest

from chaos import Final, Framework, ProviderSpec, Registry, Settings, ToolSpec
from test_computer_integration import ComputerPage


class ContextIntegrationTests(unittest.TestCase):
    def test_agent_hooks_and_tools_use_existing_effect_authority(self):
        registry=Registry();calls=[]
        def provider(request,context):
            context.checkpoint();calls.append(['agent',request.input]);return Final(request.input)
        def audit(value,context):
            context.checkpoint();calls.append(['tool',value]);return value
        registry.register_provider(ProviderSpec('context',provider));registry.register_tool(ToolSpec('audit',audit))
        source='''agent worker{provider:"context",tools:[],maxSteps:1};
class Lease{fn __enter__(self){worker("enter")}fn __exit__(self,error){worker("exit");null}}
class AsyncLease{async fn __aenter__(self){worker("async enter")}async fn __aexit__(self,error){worker("async exit");null}}
let first=with Lease() as value{tool("audit",value)};
let second=async with AsyncLease() as value{tool("audit",value)};[first,second]'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            project=app.create_project('contexts',source=source,capabilities=['audit'])
            run=app.start(project['id'],project['version'],None,mode='source')
            self.assertEqual(run.result(timeout=8),['enter','async enter'])
            events=app.events(project['id'],run_id=run.id,limit=300)['items']
            self.assertEqual(sum(e['type']=='agent.completed'for e in events),4)
        self.assertEqual(calls,[['agent','enter'],['tool','enter'],['agent','exit'],['agent','async enter'],['tool','async enter'],['agent','async exit']])

    def test_cleanup_cannot_expand_tool_authority(self):
        registry=Registry();calls=[]
        registry.register_tool(ToolSpec('forbidden',lambda value,context:calls.append(value)))
        source='class C{fn __enter__(self){null}fn __exit__(self,e){tool("forbidden",42);null}}with C() as _{1}'
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('authority',source=source,capabilities=[])
            run=app.start('authority',1,None,mode='source');self.assertEqual(run.wait(timeout=5)['status'],'failed')
        self.assertEqual(calls,[])

    def test_global_cancel_waits_for_agent_worker_and_skips_exit(self):
        entered=threading.Event();exited=threading.Event();seen=[];registry=Registry()
        def provider(request,context):
            seen.append(request.input)
            if request.input=='cleanup':return Final(None)
            entered.set()
            try:
                for _ in range(500):context.checkpoint();threading.Event().wait(.01)
                raise AssertionError('effect failed to cooperate with cancellation')
            finally:exited.set()
        registry.register_provider(ProviderSpec('cooperative',provider))
        source='''agent worker{provider:"cooperative",tools:[],maxSteps:1};
class C{async fn __aenter__(self){null}async fn __aexit__(self,e){worker("cleanup");true}}
async with C() as _{worker("body")}'''
        with Framework(registry=registry,settings=Settings(':memory:')) as app:
            app.create_project('cancel',source=source,capabilities=[]);run=app.start('cancel',1,None,mode='source')
            try:
                self.assertTrue(entered.wait(3));run.cancel();self.assertEqual(run.wait(timeout=5)['status'],'cancelled')
                self.assertTrue(exited.is_set());self.assertEqual(seen,['body'])
            finally:run.cancel()

    def test_session_handle_not_implicitly_closed(self):
        # The language rejects a durable Session as a resource manager. No model
        # request is needed to create or inspect an idle conversation.
        source='''agent worker{provider:"model",tools:[]};let session=await session_open("conversation",worker);
let code=try{async with session as _{null}}catch e{e.code};[code,(await session_status("conversation")).status]'''
        with Framework(settings=Settings(':memory:')) as app:
            app.create_project('session',source=source,capabilities=['session.manage'])
            self.assertEqual(app.start('session',1,None,mode='source').result(timeout=5),['argument_error','idle'])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ComputerContextBrowserTests(unittest.TestCase):
    def setUp(self):
        self.page=ComputerPage();self.addCleanup(self.page.close)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.settings=Settings(':memory:');self.settings.update({'runtime':{'workspace':self.tmp.name,'timeout':15}},1)

    def app(self,source):
        app=Framework(Path(self.tmp.name)/'state.sqlite3',settings=self.settings);self.addCleanup(app.close)
        app.create_project('computer',source=source,capabilities=['browser.control'])
        return app

    def test_real_browser_scope_close_precedes_next_statement(self):
        source='''let computer=await computer_open(input);let title=async with computer as c{observation_info(await computer_observe(c,false)).title};
[title,computer_info(computer).status]'''
        app=self.app(source);run=app.start('computer',1,self.page.url,mode='source')
        self.assertEqual(run.result(timeout=15),['Computer test','closed'])
        lifecycle=[r for r in app.computer_history('computer',run_id=run.id)['items'] if r['kind']=='lifecycle']
        self.assertEqual(len(lifecycle),2)
        self.assertEqual(lifecycle[-1]['data']['action'],'close')
        self.assertFalse(lifecycle[-1]['data'].get('automatic',False))

    def test_real_browser_hard_cancel_uses_host_teardown(self):
        entered=threading.Event();exited=threading.Event();registry=Registry()
        def blocked(value,context):
            entered.set()
            try:
                for _ in range(500):context.checkpoint();threading.Event().wait(.01)
            finally:exited.set()
        registry.register_tool(ToolSpec('blocked',blocked))
        source='async with await computer_open(input) as c{tool("blocked",null)}'
        with Framework(Path(self.tmp.name)/'cancel.sqlite3',settings=self.settings,registry=registry) as app:
            app.create_project('cancel',source=source,capabilities=['browser.control','blocked']);run=app.start('cancel',1,self.page.url,mode='source')
            try:
                self.assertTrue(entered.wait(8));run.cancel();self.assertEqual(run.wait(timeout=8)['status'],'cancelled');self.assertTrue(exited.is_set())
                lifecycle=[r for r in app.computer_history('cancel',run_id=run.id)['items'] if r['kind']=='lifecycle']
                self.assertEqual(lifecycle[-1]['data']['action'],'close');self.assertTrue(lifecycle[-1]['data']['automatic'])
            finally:run.cancel()
