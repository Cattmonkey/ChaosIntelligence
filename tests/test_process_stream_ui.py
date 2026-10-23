"""Actual mobile Workbench/Process UI, HTTP receipts and EventSource replay."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import unittest

from tests.process_stream_fixture import ROOT, StreamFixture, eventually
from tests.workbench_actions import button as workbench_button, view as workbench_view


class ProcessStreamControls(unittest.TestCase):
    def test_production_controller_callbacks(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node is required for Process controller acceptance')
        result = subprocess.run([node, str(ROOT/'tests/process_stream_controls.cjs'), str(ROOT)],
                                cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        for key in ('exactEventIds', 'hostReceiptProvenance', 'immutableRetry',
                    'draftSelectionPreserved', 'transcriptDisclosureFocusScrollPreserved', 'selectionScoped'):
            self.assertTrue(report[key], key)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class ProcessStreamMobile(unittest.TestCase):
    def input_state(self, locator):
        # Browser evaluation only reads DOM state; all interaction uses locators
        # and keyboard controls. There are no coordinates or global mutations.
        return locator.evaluate('e=>({value:e.value,focused:document.activeElement===e,start:e.selectionStart,end:e.selectionEnd})')

    def select_part(self, locator):
        locator.focus()
        locator.press('Home')
        locator.press('ArrowRight')
        locator.press('Shift+ArrowRight')
        locator.press('Shift+ArrowRight')

    def settled_scroll(self, page, rows, *, timeout=3):
        """Observe a stable native keyboard/focus result before a baseline.

        This polls read-only DOM state. It does not move focus, change scroll,
        mutate browser globals, or loosen the later one-pixel assertion.
        """
        deadline = time.monotonic() + timeout
        previous, equal_samples = None, 0
        while time.monotonic() < deadline:
            current = rows.evaluate('e=>({top:e.scrollTop,remaining:e.scrollHeight-e.scrollTop-e.clientHeight})')
            equal_samples = equal_samples + 1 if current == previous else 0
            if equal_samples >= 4:
                return current
            previous = current
            page.wait_for_timeout(50)
        self.fail('native reader scroll did not settle before observation: ' + repr(previous))

    def save_evidence(self, page, payload):
        output = os.environ.get('CHAOS_PROCESS_STREAM_AUDIT')
        if output:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder/'process-stream-mobile.png'), full_page=True)
            (folder/'process-stream-mobile.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def test_two_actual_bot_turns_natural_guidance_sse_replay_and_reader_state(self):
        from playwright.sync_api import sync_playwright
        with StreamFixture() as fixture:
            fixture.start()
            fixture.at_gate(1, 'progress')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context(viewport={'width':390, 'height':844})
                    errors, external, requests = [], [], []
                    context.route('http**://**', lambda route: route.continue_()
                                  if route.request.url.startswith(fixture.origin+'/')
                                  else (external.append(route.request.url), route.abort()))
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: requests.append(request.url)
                            if '/events/stream?' in request.url else None)
                    page.goto(fixture.origin+'/?project=p')
                    page.locator('.cm-editor').wait_for()
                    workbench_view(page, 'source')
                    workbench_button(page, 'processesButton')
                    dialog = page.get_by_role('dialog', name='持续进程', exact=True)
                    dialog.locator('.process-card').filter(has_text='worker').click()
                    transcript = dialog.locator('[data-process-transcript]')
                    page.wait_for_function('()=>document.querySelector("[data-process-transcript]")?.textContent.includes("public plan")')
                    self.assertIn('可信历史起于', dialog.locator('[data-process-transcript-note]').inner_text())
                    self.assertTrue(requests, 'no actual EventSource request occurred')
                    initial_tail = str(fixture.events()[-1]['id'])
                    page.wait_for_function('(id)=>ChaosProcesses.state.streams.get("p:worker").cursor===id', arg=initial_tail)

                    draft = dialog.get_by_role('textbox', name='指导文本', exact=True)
                    sent_text = '请验证 👋 <script>window.streamInjected=true</script> <img src=x onerror=evil()>'
                    draft.fill(sent_text)
                    self.select_part(draft)
                    before_input = self.input_state(draft)
                    cursor_before_gap = int(page.evaluate('ChaosProcesses.state.streams.get("p:worker").cursor'))
                    fixture.interrupt_streams()
                    page.wait_for_function('()=>document.querySelector(".process-stream-status")?.textContent.includes("重连")')
                    fixture.release(1, 'progress')
                    fixture.at_gate(1, 'adoption')
                    during_gap = [e for e in fixture.events() if e['id'] > cursor_before_gap]
                    self.assertTrue(any(e['type'] == 'bot.progress' for e in during_gap))
                    self.assertEqual(self.input_state(draft), before_input)
                    fixture.stream_allowed.set()
                    page.wait_for_function('()=>document.querySelector("[data-process-transcript]")?.textContent.includes("public progress detail")')
                    page.wait_for_function('(id)=>ChaosProcesses.state.streams.get("p:worker").cursor===id', arg=str(during_gap[-1]['id']))
                    self.assertEqual(self.input_state(draft), before_input)
                    reconnects = eventually(lambda: [r for r in fixture.stream_requests
                                                     if r['key'] == 'worker' and r['header'] is not None])
                    self.assertEqual(int(reconnects[-1]['header']), cursor_before_gap)
                    self.assertEqual(reconnects[-1]['after'], cursor_before_gap)
                    replayed_ids = transcript.locator('[data-process-event-id]').evaluate_all('nodes=>nodes.map(e=>e.dataset.processEventId)')
                    self.assertTrue({e['id'] for e in during_gap} <= {int(value) for value in replayed_ids},
                                    'replay omitted committed gap events')

                    # Open an actual replayed event and read away from the tail.
                    # PageUp is a user keyboard action on the focused summary.
                    reading_event = next(e for e in during_gap if e['type'] == 'bot.progress'
                                         and e['payload']['data'].get('summary') == 'public progress')
                    reading = transcript.locator('[data-process-event-id="'+str(reading_event['id'])+'"]')
                    summary = reading.locator('summary')
                    summary.click()
                    summary.focus()
                    summary.press('PageUp')
                    summary.press('PageUp')
                    rows = transcript.locator('[data-process-transcript-rows]')
                    read_scroll = self.settled_scroll(page, rows)
                    self.assertGreater(read_scroll['remaining'], 40, 'reader fixture must be away from auto-follow tail')
                    self.assertTrue(reading.evaluate('e=>e.open'))
                    self.assertTrue(summary.evaluate('e=>document.activeElement===e'))
                    detail_sequence = page.evaluate('ChaosProcesses.state.detailRequest')
                    # Observe the real scheduled snapshot. After the baseline
                    # there is no click, focus call or keyboard movement.
                    with page.expect_response(lambda response: response.request.method == 'GET'
                                               and response.url.endswith('/projects/p/processes/worker')) as refreshed:
                        page.wait_for_function('(serial)=>ChaosProcesses.state.detailRequest>serial', arg=detail_sequence)
                    refreshed.value.finished()
                    after_snapshot = self.settled_scroll(page, rows)
                    self.assertTrue(reading.evaluate('e=>e.open'))
                    self.assertTrue(summary.evaluate('e=>document.activeElement===e'))
                    self.assertAlmostEqual(after_snapshot['top'], read_scroll['top'], delta=1)

                    fixture.hold_next_guidance = True
                    fixture.guidance_response_allowed.clear()
                    dialog.get_by_role('button', name='发送持久指导', exact=True).click()
                    self.assertTrue(fixture.guidance_waiting.wait(5))
                    first_request = fixture.posts[-1]
                    self.assertEqual(first_request['input'], {'text': sent_text})
                    self.assertRegex(first_request['messageId'], r'^guide-[0-9a-f-]{36}$')
                    newer = '新的尚未提交文本 stays here'
                    draft.fill(newer)
                    self.select_part(draft)
                    newer_input = self.input_state(draft)
                    fixture.guidance_response_allowed.set()
                    page.wait_for_function('()=>document.querySelector("[data-process-notice]")?.textContent.includes("持久指导已确认")')
                    self.assertEqual(self.input_state(dialog.get_by_role('textbox', name='指导文本', exact=True)), newer_input)
                    summary.focus()
                    summary.press('PageUp')
                    sse_scroll = self.settled_scroll(page, rows)
                    self.assertGreater(sse_scroll['remaining'], 40)
                    fixture.release(1, 'adoption')
                    first = fixture.row(1)
                    completed_id = next(e['id'] for e in fixture.events()
                                        if e['type'] == 'process.turn.completed' and e['run_id'] == first['lastRunId'])
                    page.wait_for_function('(id)=>[...document.querySelectorAll("[data-process-event-id]")].some(e=>e.dataset.processEventId===id)', arg=str(completed_id))
                    self.assertEqual(first['state']['context']['latest'], {'text': sent_text})
                    self.assertTrue(reading.evaluate('e=>e.open'))
                    self.assertTrue(summary.evaluate('e=>document.activeElement===e'))
                    after_sse = self.settled_scroll(page, rows)
                    self.assertAlmostEqual(after_sse['top'], sse_scroll['top'], delta=1)

                    # The second natural send wakes a new actual Process run;
                    # receipt identity stays scoped to the stable Process key.
                    second_text = '第二轮 <svg onload=evil()> literal guidance'
                    draft = dialog.get_by_role('textbox', name='指导文本', exact=True)
                    draft.fill(second_text)
                    dialog.get_by_role('button', name='发送持久指导', exact=True).click()
                    fixture.at_gate(2, 'progress')
                    second_id = fixture.posts[-1]['messageId']
                    fixture.release_turn(2)
                    second = fixture.row(2)
                    second_completed_id = next(e['id'] for e in fixture.events()
                                               if e['type'] == 'process.turn.completed' and e['run_id'] == second['lastRunId'])
                    page.wait_for_function('(id)=>[...document.querySelectorAll("[data-process-event-id]")].some(e=>e.dataset.processEventId===id)', arg=str(second_completed_id))
                    self.assertNotEqual(first['lastRunId'], second['lastRunId'])
                    self.assertEqual(second['state']['context']['latest'], {'text': second_text})
                    self.assertEqual(fixture.effects, [{'text': sent_text}, {'text': second_text}])
                    self.assertEqual(fixture.client.guide_process('p', 'worker', {'text': sent_text},
                                                                 message_id=first_request['messageId'])['status'], 'applied')
                    self.assertEqual(fixture.client.guide_process('p', 'worker', {'text': second_text},
                                                                 message_id=second_id)['status'], 'applied')
                    events = fixture.events()
                    required = {'process.bot.adopt','process.bot.admit','bot.action.completed','process.bot.commit','process.turn.completed'}
                    self.assertTrue(required <= {e['type'] for e in events})
                    rendered_ids = [int(value) for value in transcript.locator('[data-process-event-id]').evaluate_all('nodes=>nodes.map(e=>e.dataset.processEventId)')]
                    self.assertEqual(rendered_ids, sorted(set(rendered_ids)), 'replay rendered duplicate or reordered original IDs')
                    retained_ids = page.evaluate('ChaosProcesses.state.streams.get("p:worker").rows.map(e=>String(e.id))')
                    self.assertTrue({e['id'] for e in during_gap} <= {int(value) for value in retained_ids})
                    queued_event = next(e for e in events if e['type'] == 'process.guidance.queued'
                                        and e['payload']['messageId'] == first_request['messageId'])
                    queued_row = transcript.locator('[data-process-event-id="'+str(queued_event['id'])+'"]')
                    queued_row.locator('summary').click()
                    rendered_queue = json.loads(queued_row.locator('pre').inner_text())
                    self.assertEqual(rendered_queue['processOrigin'], 'lifecycle')
                    self.assertEqual(rendered_queue['payload']['messageId'], first_request['messageId'])
                    self.assertNotIn('input', rendered_queue['payload'], 'queued receipt leaked its private guidance input')
                    public_result = next(e for e in events if e['type'] == 'bot.progress'
                                         and e['run_id'] == first['lastRunId']
                                         and e['payload']['kind'] == 'result')
                    result_row = transcript.locator('[data-process-event-id="'+str(public_result['id'])+'"]')
                    result_row.locator('summary').click()
                    rendered_result = json.loads(result_row.locator('pre').inner_text())
                    self.assertEqual(rendered_result['id'], public_result['id'])
                    self.assertEqual(rendered_result['processOrigin'], 'run')
                    self.assertEqual(rendered_result['payload']['data']['latest'], {'text': sent_text})
                    self.assertIn('<script>window.streamInjected=true</script>', result_row.inner_text())
                    self.assertIsNone(page.evaluate('window.streamInjected'))
                    self.assertEqual(dialog.locator('script,img,svg').count(), 0)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))

                    # Hold a real old-owner acknowledgement while the visible
                    # selection changes. A late callback must preserve the new
                    # owner's fresh forms and distinct transcript region.
                    fixture.start('other')
                    eventually(lambda: fixture.app.get_process('p', 'other')['status'] == 'waiting')
                    dialog.get_by_role('button', name='刷新状态', exact=True).click()
                    dialog.locator('.process-card').filter(has_text='other').wait_for()
                    fixture.guidance_waiting.clear()
                    fixture.guidance_response_allowed.clear()
                    fixture.hold_next_guidance = True
                    dialog.get_by_role('textbox', name='指导文本', exact=True).fill('old owner pending request')
                    dialog.get_by_role('button', name='发送持久指导', exact=True).click()
                    self.assertTrue(fixture.guidance_waiting.wait(5))
                    dialog.locator('.process-card').filter(has_text='other').click()
                    page.wait_for_function('()=>document.querySelector("[data-process-transcript]")?.dataset.processIdentity==="p:other"')
                    other_draft = dialog.get_by_role('textbox', name='指导文本', exact=True)
                    other_draft.fill('other owner draft')
                    self.select_part(other_draft)
                    other_input = self.input_state(other_draft)
                    fixture.guidance_response_allowed.set()
                    page.wait_for_function('()=>ChaosProcesses.state.messageDrafts.get("p:worker")?.naturalPending===false')
                    self.assertEqual(self.input_state(dialog.get_by_role('textbox', name='指导文本', exact=True)), other_input)
                    self.assertEqual(page.evaluate('ChaosProcesses.state.selected'), 'other')
                    self.assertEqual(page.evaluate('ChaosProcesses.state.streams.get("p:other").receipts.size'), 0)
                    dialog.locator('.process-card').filter(has_text='worker').click()
                    page.wait_for_function('()=>document.querySelector("[data-process-transcript]")?.dataset.processIdentity==="p:worker"')

                    before_detach = fixture.app.get_process('p', 'worker')
                    dialog.get_by_role('button', name='分离实时记录', exact=True).click()
                    fixture.client.guide_process('p', 'worker', {'text':'after viewer detach'}, message_id='ui-after-detach')
                    fixture.release_turn(3)
                    after_detach = fixture.row(3)
                    self.assertNotEqual(second['lastRunId'], after_detach['lastRunId'])
                    self.assertEqual(after_detach['state']['context']['latest'], {'text':'after viewer detach'})
                    self.assertEqual(fixture.control_requests, [])
                    self.assertEqual(errors, [])
                    self.assertEqual(external, [])
                    self.save_evidence(page, {'status':'passed','runIds':[first['lastRunId'],second['lastRunId']],
                        'gapCursor':cursor_before_gap,'reconnects':reconnects,'transcriptIds':rendered_ids,
                        'guidanceMessageIds':[first_request['messageId'],second_id],
                        'disclosureEventId':reading_event['id'],'partialDraftSelection':before_input,
                        'newerDraftSelection':newer_input,'readerScroll':read_scroll,'snapshotReaderScroll':after_snapshot,
                        'sseReaderScroll':sse_scroll,'afterSseReaderScroll':after_sse,
                        'otherOwnerDraftSelection':other_input,'selectedOwnerAfterDetach':before_detach['key'],
                        'remoteControls':fixture.control_requests,'pageErrors':errors,'externalRequests':external})
                finally:
                    browser.close()


if __name__ == '__main__':
    unittest.main()
