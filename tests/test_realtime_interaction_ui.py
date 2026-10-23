"""Actual public progress, guidance and assistant recovery in an isolated browser."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import urllib.parse

from chaos import Framework, Registry, Settings, ToolSpec
from chaos.server import make_server, static_path

ROOT = Path(os.environ.get('CHAOS_REALTIME_TEST_ROOT', Path(__file__).resolve().parents[1]))


class GatedGateway:
    def __init__(self, fixture):
        self.fixture = fixture
        self.chat = NS(completions=self)
        self.step = 0

    def create(self, **kwargs):
        self.fixture.model_requests.append(kwargs)
        self.step += 1
        if self.step == 1:
            self.fixture.model_entered.set()
            if not self.fixture.model_release.wait(35):
                raise AssertionError('fixture model gate timed out')
            aliases = {tool['function']['description'].split(':', 1)[0]: tool['function']['name']
                       for tool in kwargs['tools']}
            call = NS(id='public-write', function=NS(name=aliases['draft.write'],
                      arguments=json.dumps({'path': 'main.chaos', 'source': '42'})))
            message = NS(content=None, tool_calls=[call])
        else:
            message = NS(content='已完成公开工具检查；请审阅提案。', tool_calls=[])
        return NS(choices=[NS(message=message)])

    def close(self):
        pass


class InteractionFixture:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='chaos-realtime-ui-')
        self.root = Path(self.tmp.name)
        self.model_entered = threading.Event()
        self.model_release = threading.Event()
        self.run_entered = threading.Event()
        self.run_release = threading.Event()
        self.model_requests = []
        self.sockets = set()
        self.socket_lock = threading.Lock()
        self.stream_allowed = True
        self.stream_gate = threading.Event()
        self.stream_gate.set()
        self.stream_gate_entered = threading.Event()
        self.stream_requests = []
        registry = Registry()

        def gate(value, context):
            self.run_entered.set()
            deadline = time.monotonic() + 35
            while not self.run_release.wait(.01):
                context.checkpoint()
                if time.monotonic() >= deadline:
                    raise AssertionError('fixture run gate timed out')
            return value

        registry.register_tool(ToolSpec('fixture.reader_gate', gate))
        settings = Settings(':memory:', client_factory=lambda config: GatedGateway(self))
        settings.update({'providers': {'text': {'baseURL': 'https://fixture.invalid/v1',
                         'apiKey': 'fixture-not-real'}}, 'runtime': {'timeout': 60}}, 1)
        self.app = Framework(settings=settings, registry=registry)
        self.source = '''for i in range(90) {await progress_emit("progress", {summary:"initial "+str(i)})};
tool("fixture.reader_gate", null);
for i in range(8) {await progress_emit("progress", {summary:"new batch "+str(i)})};
let guide = await guidance_receive(null);
await progress_emit("result", {summary:"guidance completed",input:guide.message.input});
guide.message.input'''
        self.app.create_project('progress', source=self.source, capabilities=['fixture.reader_gate'])
        self.app.create_project('assistant', source='40')
        self.asset_patch = patch('chaos.server.static_path',
                                 lambda name: ROOT/'web'/name if (ROOT/'web'/name).is_file() else static_path(name))
        self.asset_patch.start()
        self.server = make_server('127.0.0.1', 0, framework=self.app,
                                  state_dir=self.root/'state', update_home=self.root/'updates')
        fixture = self
        production = self.server.RequestHandlerClass

        class FaultHandler(production):
            def activity_stream(self, manager, after, task_id=None, read=None):
                selected = '/assistant/' in urllib.parse.urlsplit(self.path).path
                if selected:
                    fixture.stream_requests.append({'after': after, 'taskId': task_id})
                    if not fixture.stream_allowed:
                        return self.respond(503, {'error': 'fixture stream disconnected'})
                    fixture.stream_gate_entered.set()
                    if not fixture.stream_gate.wait(20):
                        return self.respond(503, {"error": "fixture SSE gate timed out"})
                    with fixture.socket_lock:
                        fixture.sockets.add(self.connection)
                try:
                    return super().activity_stream(manager, after, task_id, read)
                finally:
                    if selected:
                        with fixture.socket_lock:
                            fixture.sockets.discard(self.connection)

        self.server.RequestHandlerClass = FaultHandler
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        return self

    def disconnect_assistant(self):
        self.stream_allowed = False
        with self.socket_lock:
            sockets = list(self.sockets)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def __exit__(self, *args):
        self.model_release.set()
        self.run_release.set()
        self.stream_gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()
        self.asset_patch.stop()
        self.tmp.cleanup()


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Python Playwright unavailable')
class RealtimeInteractionUI(unittest.TestCase):
    def evidence(self, page, name, payload):
        directory = os.environ.get('CHAOS_REALTIME_AUDIT')
        if directory:
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder/(name+'.png')), full_page=True)
            (folder/(name+'.json')).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def command(self, page, identifier):
        page.locator('#wbCommandButton').click()
        page.locator('#wbCommandInput').fill(identifier)
        page.locator('#wbCommandList [role="option"]').first.click()
        page.locator('#wbCommands').wait_for(state='hidden')

    def edit(self, page, text):
        editor = page.locator('.cm-content').first
        editor.click()
        editor.press('Meta+A' if sys.platform == 'darwin' else 'Control+A')
        editor.fill(text)

    def test_progress_guidance_reader_follow_and_mobile(self):
        from playwright.sync_api import sync_playwright
        with InteractionFixture() as fixture, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page.set_default_timeout(12000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(fixture.origin+'/?project=progress')
                page.locator('.cm-content').first.wait_for()
                self.command(page, 'run.options')
                if page.locator('#expressionTrace').is_checked():
                    page.locator('#expressionTrace').uncheck()
                page.locator('#runButton').click()
                self.assertTrue(fixture.run_entered.wait(7))
                self.command(page, 'output.events')
                panel = page.locator('#eventsPanel')
                page.wait_for_function('()=>document.querySelector("#eventsPanel").textContent.includes("initial 89")')
                panel.hover()
                page.mouse.wheel(0, -10000)
                page.wait_for_function('()=>document.querySelector("#eventsPanel").scrollTop<10')
                before = panel.evaluate('e=>({top:e.scrollTop,remaining:e.scrollHeight-e.scrollTop-e.clientHeight})')
                self.assertGreater(before['remaining'], 40)
                fixture.run_release.set()
                page.wait_for_function('()=>document.querySelector("#eventsPanel").textContent.includes("new batch 7")')
                after = panel.evaluate('e=>e.scrollTop')
                self.assertAlmostEqual(after, before['top'], delta=1)
                page.locator('#followEvents').wait_for(state='visible')
                page.locator('#followEvents').click()
                page.wait_for_function('()=>{const e=document.querySelector("#eventsPanel");return e.scrollHeight-e.scrollTop-e.clientHeight<32}')
                self.command(page, 'run.guide')
                guide = '请验证 👋 <script>window.injected=true</script>'
                page.locator('#runGuidanceInput').fill(guide)
                page.locator('#runGuidanceSend').click()
                page.wait_for_function('()=>document.querySelector("#runGuidanceReceipt").textContent.includes("运行已接收")')
                page.wait_for_function('()=>document.querySelector("#eventsPanel").textContent.includes("guidance completed")')
                rows = fixture.app.runs('progress')['items']
                run = fixture.app.get_run(rows[0]['id'])
                self.assertEqual(run['output'], {'text': guide})
                self.assertIsNone(page.evaluate('window.injected'))
                events, cursor = [], 0
                while True:
                    event_page = fixture.app.events('progress', run_id=run['id'], after=cursor, limit=300)
                    events.extend(event_page['items'])
                    if not event_page['nextCursor']:
                        break
                    cursor = event_page['nextCursor']
                self.assertEqual([e['type'] for e in events if e['type'].startswith('guidance.')],
                                 ['guidance.queued', 'guidance.received'])
                self.command(page, 'run.close')
                self.evidence(page, 'progress-desktop', {'status': 'passed', 'readerBefore': before,
                                                       'readerAfter': after, 'guidance': 'received', 'errors': errors})
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                self.evidence(page, 'progress-mobile', {'status': 'passed', 'width': 390, 'errors': errors})
                self.assertEqual(errors, [])
                self.assertEqual(fixture.model_requests, [])
            finally:
                browser.close()

    def test_assistant_disconnect_replays_progress_without_losing_drafts(self):
        from playwright.sync_api import sync_playwright
        with InteractionFixture() as fixture, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page = context.new_page()
                page.set_default_timeout(12000)
                errors, blocked_gets = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                offline = {'value': False}
                def snapshots(route):
                    request = route.request
                    if offline['value'] and request.method == 'GET' and not request.url.endswith('/events/stream'):
                        blocked_gets.append(request.url)
                        route.abort('connectionfailed')
                    else:
                        route.continue_()
                context.route('**/api/projects/assistant/assistant/*', snapshots)
                page.goto(fixture.origin+'/?project=assistant')
                page.locator('.cm-content').first.wait_for()
                page.locator('#ideContextAssistant').click()
                page.locator('#ideChatPrompt').fill('修改为 42 并提供可复核的提案')
                page.locator('#ideChatSend').click()
                self.assertTrue(fixture.model_entered.wait(7))
                page.locator('.ide-job-progress').wait_for()
                job_id = page.locator('[data-job]').first.get_attribute('data-job')
                offline['value'] = True
                fixture.disconnect_assistant()
                page.wait_for_function('()=>document.querySelector("#ideAssistantState").textContent.includes("恢复")')
                deadline = time.monotonic()+9
                while not blocked_gets and time.monotonic()<deadline:
                    page.wait_for_timeout(50)
                self.assertTrue(blocked_gets, 'snapshot GET must actually fail while SSE is disconnected')
                self.assertTrue(page.locator('#ideChatSend').is_disabled())
                self.assertTrue(page.locator('#ideChatCancel').is_visible())
                prompt = '断线期间继续写的新请求，不应被清除'
                page.locator('#ideChatPrompt').fill(prompt)
                self.edit(page, '99 // preserved unsaved draft')
                fixture.model_release.set()
                deadline = time.monotonic()+7
                while fixture.server.ide.get('assistant', job_id)['status'] in ('queued', 'running') and time.monotonic()<deadline:
                    page.wait_for_timeout(25)
                self.assertEqual(fixture.server.ide.get('assistant', job_id)['status'], 'completed')
                fixture.stream_allowed = True
                offline['value'] = False
                page.locator('.ide-apply').wait_for()
                self.assertEqual(page.locator('[data-job]').count(), 1)
                self.assertEqual(page.locator('#ideChatPrompt').input_value(), prompt)
                self.assertIn('99 // preserved unsaved draft', page.locator('.cm-content').first.inner_text())
                self.assertEqual(fixture.app.get_project('assistant')['source'], '40')
                self.assertEqual(fixture.app.get_project('assistant')['version'], 1)
                self.assertTrue(page.locator('#ideChatCancel').is_hidden())
                self.assertTrue(page.locator('#ideChatSend').is_enabled())
                progress = page.locator('.ide-job-progress')
                progress.locator('summary').click()
                ids = progress.locator('[data-event-id]').evaluate_all('rows=>rows.map(e=>e.dataset.eventId)')
                self.assertEqual(len(ids), len(set(ids)), 'replayed progress must appear once')
                completed = [e for e in fixture.server.ide.manager.events(task_id=job_id) if e['type']=='task.completed']
                self.assertEqual(len(completed), 1)
                self.assertIn(str(completed[0]['id']), ids, 'recovery must deliver the terminal public event before detaching')
                self.assertEqual(page.get_by_text('已完成公开工具检查；请审阅提案。', exact=True).count(), 1)
                self.evidence(page, 'assistant-recovery-desktop', {'status': 'passed', 'jobId': job_id,
                    'blockedGETs': len(blocked_gets), 'eventIds': ids, 'streamRequests': fixture.stream_requests,
                    'draftPreserved': True, 'automaticallyApplied': False, 'errors': errors})
                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('#ideContextAssistant').click()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                self.evidence(page, 'assistant-recovery-mobile', {'status': 'passed', 'width': 390, 'errors': errors})
                self.assertEqual(errors, [])
                self.assertEqual(len(fixture.model_requests), 2)
            finally:
                browser.close()


    def test_assistant_terminal_snapshot_waits_for_complete_event_tail(self):
        from playwright.sync_api import sync_playwright
        with InteractionFixture() as fixture, sync_playwright() as playwright:
            fixture.stream_gate.clear()
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
                page.set_default_timeout(12000)
                errors = []
                terminal_snapshots = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                def snapshot(response):
                    path = urllib.parse.urlsplit(response.url).path
                    prefix = '/api/projects/assistant/assistant/'
                    if (response.request.method == 'GET' and path.startswith(prefix)
                            and '/' not in path[len(prefix):] and response.status == 200):
                        value = response.json()
                        if value.get('status') == 'completed':
                            terminal_snapshots.append(value['id'])
                page.on('response', snapshot)
                page.goto(fixture.origin+'/?project=assistant')
                page.locator('.cm-content').first.wait_for()
                page.locator('#ideContextAssistant').click()
                page.locator('#ideChatPrompt').fill('修改为 42 并等待完整公开进度')
                page.locator('#ideChatSend').click()
                self.assertTrue(fixture.model_entered.wait(7))
                page.locator('[data-job]').first.wait_for()
                self.assertTrue(fixture.stream_gate_entered.wait(7))
                job_id = page.locator('[data-job]').first.get_attribute('data-job')
                fixture.model_release.set()
                page.locator('.ide-apply').wait_for()
                self.assertTrue(terminal_snapshots, 'the actual terminal GET must precede SSE delivery')
                self.assertEqual(page.locator('.ide-job-progress [data-event-id]').count(), 0)
                self.assertTrue(page.locator('#ideChatSend').is_disabled())
                self.assertTrue(page.locator('#ideChatCancel').is_visible())
                self.assertIn('同步最后', page.locator('#ideAssistantState').inner_text())
                self.evidence(page, 'assistant-terminal-before-stream', {
                    'status': 'waiting-for-event-tail', 'terminalSnapshots': len(terminal_snapshots),
                    'deliveredEvents': 0, 'sendDisabled': True, 'errors': errors})
                fixture.stream_gate.set()
                page.wait_for_function('()=>!document.querySelector("#ideChatSend").disabled')
                progress = page.locator('.ide-job-progress')
                progress.locator('summary').click()
                ids = progress.locator('[data-event-id]').evaluate_all('rows=>rows.map(e=>e.dataset.eventId)')
                events = fixture.server.ide.manager.events(task_id=job_id)
                self.assertEqual(ids, [str(event['id']) for event in events], 'every persisted public event must be delivered in order')
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual([e['type'] for e in events].count('task.completed'), 1)
                self.assertTrue(page.locator('#ideChatCancel').is_hidden())
                self.assertEqual(page.get_by_text('已完成公开工具检查；请审阅提案。', exact=True).count(), 1)
                self.assertEqual(fixture.app.get_project('assistant')['source'], '40')
                self.assertEqual(fixture.app.get_project('assistant')['version'], 1)
                self.assertEqual(errors, [])
                self.assertEqual(len(fixture.model_requests), 2)
                self.evidence(page, 'assistant-terminal-stream-drained', {
                    'status': 'passed', 'jobId': job_id, 'eventIds': ids,
                    'terminalSnapshots': len(terminal_snapshots), 'streamRequests': fixture.stream_requests,
                    'automaticallyApplied': False, 'errors': errors})
            finally:
                fixture.stream_gate.set()
                browser.close()


if __name__ == '__main__':
    unittest.main()
