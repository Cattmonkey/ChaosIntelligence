"""Existing run composer and public event stream send real cooperative guidance."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from tests import test_workbench_ui as workbench

ROOT=Path(__file__).resolve().parents[1]


class BotGuidanceUIControls(unittest.TestCase):
    def test_actual_production_composer_retains_text_receipts_and_event_identity(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node required for guidance UI acceptance')
        result=subprocess.run([node,str(ROOT/'tests/bot_guidance_controls.cjs'),str(ROOT)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr);report=json.loads(result.stdout);self.assertEqual(report['status'],'passed');self.assertTrue(report['draftPreserved']);self.assertTrue(report['receiptNotDowngraded'])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class BotGuidanceUI(unittest.TestCase):
    setUp=workbench.WorkbenchUI.setUp
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def test_real_active_run_guidance_public_progress_and_mobile_composer(self):
        source='await progress_emit("plan",{summary:"等待补充目标",steps:["receive","verify"]});let item=await guidance_receive(null);await progress_emit("progress",{summary:"补充信息已接收"});await sleep(0.2);await progress_emit("result",{summary:"执行完成",input:item.message.input});item.message.input'
        self.draft(source);self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        saved=self.app.get_project('workbench');self.page.locator('#runButton').click();self.page.wait_for_function('()=>busy&&activeRunId&&document.querySelector("#eventsPanel")?.textContent.includes("等待补充目标")')
        self.command('run.guide');self.assertTrue(self.page.locator('#runGuidanceForm').is_visible())
        self.draft(source+'; // unsaved draft remains');text='请验证 👋 <script>window.guidanceInjected=true</script>'
        self.page.locator('#runGuidanceInput').fill(text);self.page.locator('#runGuidanceSend').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#runGuidanceReceipt")?.textContent.includes("运行已接收")')
        run=self.app.get_run(self.page.evaluate('currentRun'));self.assertEqual(run['output'],{'text':text});self.assertEqual(run['version'],saved['version'])
        self.assertTrue(self.page.evaluate('dirty'));self.assertIsNone(self.page.evaluate('window.guidanceInjected'))
        events=self.app.events('workbench',run_id=run['id'])['items'];self.assertEqual([e['type']for e in events if e['type'].startswith('guidance.')],['guidance.queued','guidance.received'])
        self.assertEqual([e['payload']['kind']for e in events if e['type']=='bot.progress'],['plan','progress','result'])
        self.assertIn('执行完成',self.page.locator('#eventsPanel').text_content());self.page.set_viewport_size({'width':390,'height':844});self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
