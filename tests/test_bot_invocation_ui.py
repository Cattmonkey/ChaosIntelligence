"""Actual host invocation picker, literal delivery and stale browser boundaries."""
from contextlib import ExitStack
import importlib.util
import unittest

from test_bot_invocation_surfaces import SOURCE, service, targets_for
from workbench_actions import button as workbench_button, command as workbench_command


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class BotInvocationUI(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.resources = ExitStack()
        self.app, self.client, self.gate, self.origin = self.resources.enter_context(service())
        self.pw = sync_playwright().start()
        self.resources.callback(self.pw.stop)
        self.browser = self.pw.chromium.launch(headless=True)
        self.resources.callback(self.browser.close)
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.errors, self.external = [], []
        def local(route):
            if route.request.url.startswith(self.origin + '/'):
                route.continue_()
            else:
                self.external.append(route.request.url); route.abort()
        self.context.route('http**://**', local)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.origin + '/?project=scopes')
        self.page.wait_for_function('()=>window.ChaosIDE?.editor()&&window.ChaosWorkbench&&project?.id==="scopes"')
        self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source', arg=SOURCE)

    def tearDown(self):
        try:
            self.assertEqual(self.errors, [])
            self.assertEqual(self.external, [])
        finally:
            self.resources.close()

    def _start(self):
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>busy&&activeRunId')
        run_id = self.page.evaluate('activeRunId')
        self.assertTrue(self.gate.entered['A'].wait(5)); self.assertTrue(self.gate.entered['B'].wait(5))
        a, b = targets_for(self.app, run_id)
        workbench_command(self.page, 'run.guide')
        for target in (a, b):
            self.page.locator('#runGuidanceTarget option[value="' + target['scopeId'] + '"]').wait_for(state='attached')
        return run_id, a, b

    def _select(self, target):
        self.page.locator('#runGuidanceTarget').select_option(target['scopeId'])
        self.assertEqual(self.page.locator('#runGuidanceTarget').input_value(), target['scopeId'])

    def test_real_picker_delivers_each_literal_input_without_editing_saved_program(self):
        run_id, a, b = self._start()
        draft = SOURCE + '\n// 尚未保存的源码保留\n'
        self.page.evaluate('''text=>{const e=ChaosIDE.editor();e.dispatch({changes:{from:0,to:e.state.doc.length,insert:text}})}''', draft)
        text_a = '@B PASS <script>window.botScopeInjected=true</script> 原样输入 A'
        text_b = '这是 B 的补充约束 👋'
        for target, text in ((a, text_a), (b, text_b)):
            self._select(target)
            self.page.locator('#runGuidanceInput').fill(text)
            self.page.locator('#runGuidanceSend').click()
            self.page.wait_for_function('()=>!document.querySelector("#runGuidanceSend").disabled&&document.querySelector("#runGuidanceInput").value===""')
            self.assertIn('指导 r1', self.page.locator('#runGuidanceReceipt').inner_text())
        self.gate.release.set()
        self.page.wait_for_function('()=>!busy&&!launching')
        run = self.app.get_run(run_id)
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['output']['answers'], [{'text': text_a}, {'text': text_b}])
        self.assertEqual(run['version'], 1)
        self.assertEqual(self.app.get_project('scopes')['files']['main.chaos'], SOURCE)
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'), draft)
        self.assertTrue(self.page.evaluate('dirty'))
        self.assertIsNone(self.page.evaluate('window.botScopeInjected'))
        queued = [event for event in self.app.events('scopes', run_id=run_id, limit=300)['items']
                  if event['type'] == 'guidance.queued']
        self.assertEqual({event['payload']['target']['scopeId'] for event in queued}, {a['scopeId'], b['scopeId']})
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))

    def test_late_real_post_response_cannot_clear_another_targets_draft_or_receipt(self):
        run_id, a, b = self._start()
        held = []
        def hold_post(route):
            if route.request.method == 'POST':
                response = route.fetch()
                held.append((route, response))
            else:
                route.continue_()
        self.page.route('**/runs/' + run_id + '/guidance', hold_post)
        self._select(a)
        first = '先给 A 的真实指导'
        self.page.locator('#runGuidanceInput').fill(first)
        with self.page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/runs/' + run_id + '/guidance')):
            self.page.locator('#runGuidanceSend').click()
        self._select(b)
        second = '等待 A 回执期间编辑 B，必须保留'
        self.page.locator('#runGuidanceInput').fill(second)
        self.assertTrue(held, 'real server POST response must be held')
        for route, response in held:
            route.fulfill(response=response)
        self.page.unroute('**/runs/' + run_id + '/guidance', hold_post)
        self.page.wait_for_function('()=>!document.querySelector("#runGuidanceSend").disabled')
        self.assertEqual(self.page.locator('#runGuidanceInput').input_value(), second)
        self.assertNotIn('指导 r1', self.page.locator('#runGuidanceReceipt').inner_text())
        self._select(a)
        self.assertEqual(self.page.locator('#runGuidanceInput').input_value(), '')
        self.assertIn('指导 r1', self.page.locator('#runGuidanceReceipt').inner_text())
        self._select(b)
        self.assertEqual(self.page.locator('#runGuidanceInput').input_value(), second)
        self.page.locator('#runGuidanceSend').click()
        self.page.wait_for_function('()=>document.querySelector("#runGuidanceInput").value===""')
        self.gate.release.set()
        self.page.wait_for_function('()=>!busy&&!launching')
        self.assertEqual(self.app.get_run(run_id)['output']['answers'], [{'text': first}, {'text': second}])

    def test_held_real_scope_snapshot_cannot_repopulate_a_new_runs_picker(self):
        old_run, old_a, old_b = self._start()
        held = []
        pattern = '**/runs/' + old_run + '/guidance'
        def hold_get(route):
            if route.request.method == 'GET':
                held.append((route, route.fetch()))
            else:
                route.continue_()
        self.page.route(pattern, hold_get)
        with self.page.expect_request(lambda request: request.method == 'GET' and request.url.endswith('/runs/' + old_run + '/guidance')):
            self.page.evaluate('()=>{void ChaosRunGuidance.refreshScopes()}')
        workbench_button(self.page, 'cancelButton')
        self.page.wait_for_function('()=>!busy&&!launching')
        self.assertTrue(held)
        self.page.locator('#runButton').click()
        self.page.wait_for_function('(old)=>busy&&activeRunId&&activeRunId!==old', arg=old_run)
        new_run = self.page.evaluate('activeRunId')
        new_a, new_b = targets_for(self.app, new_run)
        for target in (new_a, new_b):
            self.page.locator('#runGuidanceTarget option[value="' + target['scopeId'] + '"]').wait_for(state='attached')
        for route, response in held:
            route.fulfill(response=response)
        self.page.unroute(pattern, hold_get)
        self._select(new_b)
        self.page.locator('#runGuidanceInput').fill('新运行的草稿')
        self.page.evaluate('()=>ChaosRunGuidance.refresh()')
        options = self.page.locator('#runGuidanceTarget option').evaluate_all('items=>items.map(item=>item.value)')
        self.assertIn(new_a['scopeId'], options); self.assertIn(new_b['scopeId'], options)
        self.assertNotIn(old_a['scopeId'], options); self.assertNotIn(old_b['scopeId'], options)
        self.assertEqual(self.page.locator('#runGuidanceInput').input_value(), '新运行的草稿')
        self.assertEqual(self.page.evaluate('activeRunId'), new_run)
        workbench_button(self.page, 'cancelButton')
        self.page.wait_for_function('()=>!busy&&!launching')


if __name__ == '__main__':
    unittest.main()
