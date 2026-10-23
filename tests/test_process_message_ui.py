"""Real local Process message lookup with actual held HTTP responses."""
from contextlib import ExitStack
import importlib.util
import json
import unittest

from tests.test_process_message_surfaces import outcome_service
from tests.workbench_actions import button as workbench_button, view as workbench_view


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class ProcessMessageUI(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.resources=ExitStack();self.addCleanup(self.resources.close)
        self.app,self.client,self.origin=self.resources.enter_context(outcome_service())
        self.pw=sync_playwright().start();self.resources.callback(self.pw.stop)
        self.browser=self.pw.chromium.launch(headless=True);self.resources.callback(self.browser.close)
        self.context=self.browser.new_context(viewport={'width':1440,'height':1000})
        self.errors=[];self.external=[]
        self.context.route('http**://**',lambda route:route.continue_()if route.request.url.startswith(self.origin+'/')else(self.external.append(route.request.url),route.abort()))
        self.page=self.context.new_page();self.page.set_default_timeout(10000)
        self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.page.goto(self.origin+'/?project=p');self.page.locator('.cm-editor').wait_for()
        workbench_view(self.page,'source');workbench_button(self.page,'processesButton')
        self.dialog=self.page.get_by_role('dialog',name='持续进程',exact=True)
        self.dialog.locator('.process-card').filter(has_text='worker').click()
        self.box=self.dialog.locator('[data-process-message-outcome]')
        self.box.locator('summary').click()

    def tearDown(self):
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])

    def lookup(self,ident):
        self.box.get_by_label('查询消息 ID',exact=True).fill(ident)
        self.box.get_by_role('button',name='读取消息结果',exact=True).click()
        self.page.wait_for_function('(id)=>{const text=document.querySelector("[data-process-message-result]")?.textContent;return text&&JSON.parse(text).messageId===id}',arg=ident)
        return json.loads(self.box.locator('[data-process-message-result]').inner_text())

    def test_exact_old_message_output_and_original_run_link_remain_visible_after_poll(self):
        first=self.lookup('first');self.assertEqual(first,self.client.get_process_message('p','worker','first'))
        self.assertEqual(first['run']['output'],{'state':2,'output':21,'done':False})
        self.assertEqual(self.app.get_process('p','worker')['output'],42)
        self.assertIn('邮箱 consumed',self.box.inner_text());self.assertIn('运行 completed',self.box.inner_text())
        self.assertTrue(self.box.get_by_role('button',name='查看该消息的运行',exact=True).is_visible())
        field=self.box.get_by_label('查询消息 ID',exact=True)
        field.focus();field.press('Home');field.press('ArrowRight');field.press('Shift+ArrowRight')
        self.page.evaluate('()=>ChaosProcesses.refresh()')
        self.assertTrue(self.box.locator('[data-process-message-result]').is_visible())
        self.assertTrue(self.box.get_by_role('button',name='查看该消息的运行',exact=True).is_visible())
        self.assertEqual(field.evaluate('e=>({focused:document.activeElement===e,start:e.selectionStart,end:e.selectionEnd})'),{'focused':True,'start':1,'end':2})
        self.assertEqual(self.box.get_by_label('查询消息 ID',exact=True).input_value(),'first')
        self.assertEqual(json.loads(self.box.locator('[data-process-message-result]').inner_text()),first)
        second=self.lookup('second');self.assertEqual(second['run']['output']['output'],42)
        self.assertNotEqual(first['run']['id'],second['run']['id'])
        self.assertEqual(self.app.get_project('p')['version'],1)

    def test_late_old_response_and_mismatched_identity_cannot_replace_current_result(self):
        held=[];pattern='**/projects/p/processes/worker/messages/first'
        def hold(route):held.append((route,route.fetch()))
        self.page.route(pattern,hold)
        self.box.get_by_label('查询消息 ID',exact=True).fill('first')
        with self.page.expect_request(lambda r:r.url.endswith('/processes/worker/messages/first')):
            self.box.get_by_role('button',name='读取消息结果',exact=True).click()
        field=self.box.get_by_label('查询消息 ID',exact=True)
        field.fill('second');field.press('Home');field.press('ArrowRight');field.press('Shift+ArrowRight')
        self.page.evaluate('()=>ChaosProcesses.refresh()')
        self.assertTrue(field.is_visible())
        self.assertEqual(field.evaluate('e=>({focused:document.activeElement===e,start:e.selectionStart,end:e.selectionEnd})'),{'focused':True,'start':1,'end':2})
        self.box.get_by_role('button',name='读取消息结果',exact=True).click()
        self.page.wait_for_function('()=>{const t=document.querySelector("[data-process-message-result]")?.textContent;return t&&JSON.parse(t).messageId==="second"}')
        self.assertTrue(held,'the actual first-message response must be held')
        for route,response in held:route.fulfill(response=response)
        self.page.unroute(pattern,hold)
        self.page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        self.assertEqual(json.loads(self.box.locator('[data-process-message-result]').inner_text())['messageId'],'second')
        # Exercise a genuine HTTP response whose identity was corrupted only at
        # the browser delivery boundary; it must be rejected, never shown.
        def wrong(route):
            response=route.fetch();body=response.json();body['messageId']='wrong-message'
            route.fulfill(response=response,json=body)
        self.page.route(pattern,wrong)
        self.box.get_by_label('查询消息 ID',exact=True).fill('first')
        self.box.get_by_role('button',name='读取消息结果',exact=True).click()
        self.page.wait_for_function('()=>document.querySelector("[data-process-message-outcome]")?.textContent.includes("消息结果身份或格式不匹配")')
        self.assertEqual(self.box.locator('[data-process-message-result]').inner_text(),'')
        self.assertEqual(self.box.get_by_label('查询消息 ID',exact=True).input_value(),'first')
        self.assertEqual(self.app.get_process('p','worker')['output'],42)


    def test_held_response_for_another_process_cannot_replace_same_id_result(self):
        held=[];pattern='**/projects/p/processes/worker/messages/first'
        def hold(route):held.append((route,route.fetch()))
        self.page.route(pattern,hold)
        self.box.get_by_label('查询消息 ID',exact=True).fill('first')
        with self.page.expect_request(lambda r:r.url.endswith('/processes/worker/messages/first')):
            self.box.get_by_role('button',name='读取消息结果',exact=True).click()
        self.dialog.locator('.process-card').filter(has_text='other').click()
        self.page.wait_for_function('()=>ChaosProcesses.state.selected==="other"&&ChaosProcesses.state.record?.key==="other"')
        self.box.locator('summary').click()
        result=self.lookup('first');self.assertEqual(result['key'],'other')
        self.assertEqual(result['run']['output']['output'],99)
        self.assertTrue(held)
        for route,response in held:route.fulfill(response=response)
        self.page.unroute(pattern,hold)
        self.page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        final=json.loads(self.box.locator('[data-process-message-result]').inner_text())
        self.assertEqual(final['key'],'other');self.assertEqual(final['run']['output']['output'],99)
        self.assertEqual(self.box.get_by_label('查询消息 ID',exact=True).input_value(),'first')


if __name__=='__main__':unittest.main()
