"""Real offline role picker and held HTTP boundaries; no model or device input."""
from contextlib import ExitStack
import importlib.util
import unittest

from test_process_roles_surfaces import service, roles
from workbench_actions import button as workbench_button, view as workbench_view


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ProcessRoleUI(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.resources=ExitStack()
        self.app,self.client,self.origin=self.resources.enter_context(service())
        self.pw=sync_playwright().start();self.resources.callback(self.pw.stop)
        self.browser=self.pw.chromium.launch(headless=True);self.resources.callback(self.browser.close)
        self.context=self.browser.new_context(viewport={'width':1440,'height':1000})
        self.errors,self.external=[],[]
        self.context.route('http**://**',lambda route:route.continue_()if route.request.url.startswith(self.origin+'/')else(self.external.append(route.request.url),route.abort()))
        self.page=self.context.new_page();self.page.set_default_timeout(10000)
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.goto(self.origin+'/?project=p');self.page.locator('.cm-editor').wait_for()
        workbench_view(self.page,'source');workbench_button(self.page,'processesButton')
        self.dialog=self.page.get_by_role('dialog',name='持续进程',exact=True)
        self.dialog.locator('.process-card').filter(has_text='worker').click()
        self.by_key=roles(self.client)
        self._select('left')

    def tearDown(self):
        try:self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        finally:self.resources.close()

    def _select(self,key):
        role_id=self.by_key[key]['roleId']
        self.dialog.locator('[data-process-role-target] option[value="'+role_id+'"]').wait_for(state='attached')
        self.dialog.get_by_label('持久指导目标').select_option(role_id)
        self.assertEqual(self.dialog.get_by_label('持久指导目标').input_value(),role_id)

    def test_actual_offline_role_post_receipt_cannot_clear_another_roles_draft(self):
        held=[];pattern='**/projects/p/processes/worker/guidance'
        def hold_post(route):
            if route.request.method=='POST':held.append((route,route.fetch()))
            else:route.continue_()
        self.page.route(pattern,hold_post)
        first='@right PASS <script>window.roleInjected=true</script> 👋'
        self.dialog.get_by_label('指导文本',exact=True).fill(first)
        with self.page.expect_request(lambda request:request.method=='POST'and request.url.endswith('/processes/worker/guidance')):
            self.dialog.get_by_role('button',name='发送持久指导',exact=True).click()
        self._select('right');second='B 的未提交输入必须保持'
        self.dialog.get_by_label('指导文本',exact=True).fill(second)
        self.assertTrue(held,'real accepted HTTP response must be held')
        self.assertIsNone(held[0][1].json()['runId'])
        self.assertEqual(held[0][1].json()['target'],self.by_key['left']['target'])
        for route,response in held:route.fulfill(response=response)
        self.page.unroute(pattern,hold_post)
        self.page.wait_for_function('()=>[...ChaosProcesses.state.roleDrafts.values()].some(x=>x.naturalPending===false&&x.guideReceipt)')
        self.assertEqual(self.dialog.get_by_label('指导文本',exact=True).input_value(),second)
        self.assertNotIn('指导 r1',self.dialog.locator('[data-process-natural-receipt]').inner_text())
        self._select('left');self.assertEqual(self.dialog.get_by_label('指导文本',exact=True).input_value(),'')
        self.assertIn('指导 r1',self.dialog.locator('[data-process-natural-receipt]').inner_text())
        self._select('right');self.assertEqual(self.dialog.get_by_label('指导文本',exact=True).input_value(),second)
        self.dialog.get_by_role('button',name='发送持久指导',exact=True).click()
        self.page.wait_for_function('()=>[...document.querySelectorAll("textarea")].some(e=>e.getAttribute("aria-label")==="指导文本"&&e.value==="")')
        self.assertEqual(roles(self.client)['left']['pendingCount'],1)
        self.assertEqual(roles(self.client)['right']['pendingCount'],1)
        self.assertIsNone(self.page.evaluate('window.roleInjected'))
        self.assertEqual(self.app.get_project('p')['version'],1)
        self.assertEqual(self.app.get_process('p','worker')['state'],{'kept':True})

    def test_held_actual_registry_cannot_replace_another_process_targets_or_draft(self):
        held=[];pattern='**/projects/p/processes/worker/guidance'
        def hold_get(route):
            if route.request.method=='GET':held.append((route,route.fetch()))
            else:route.continue_()
        self.page.route(pattern,hold_get)
        with self.page.expect_request(lambda request:request.method=='GET'and request.url.endswith('/processes/worker/guidance')):
            self.page.evaluate('()=>{void ChaosProcesses.refresh()}')
        self.dialog.locator('.process-card').filter(has_text='other').click()
        observer=roles(self.client,'other')['observer']['roleId']
        self.dialog.locator('[data-process-role-target] option[value="'+observer+'"]').wait_for(state='attached')
        self.dialog.get_by_label('持久指导目标').select_option(observer)
        self.dialog.get_by_label('指导文本',exact=True).fill('other 的独立草稿')
        self.assertTrue(held)
        for route,response in held:route.fulfill(response=response)
        self.page.unroute(pattern,hold_get)
        self.page.wait_for_function('()=>ChaosProcesses.state.selected==="other"')
        options=self.dialog.locator('[data-process-role-target] option').evaluate_all('items=>items.map(x=>x.value)')
        self.assertIn(observer,options)
        self.assertNotIn(self.by_key['left']['roleId'],options)
        self.assertEqual(self.dialog.get_by_label('指导文本',exact=True).input_value(),'other 的独立草稿')
        self.assertEqual(self.dialog.get_by_label('持久指导目标').input_value(),observer)


if __name__=='__main__':unittest.main()
