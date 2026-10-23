"""Native Bot reuses the existing composer, source editor and event renderer."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from chaos import Framework, Registry
from tests import test_workbench_ui as workbench
from test_bot_ui_guidance_timing import hold_bot_plan_until_guide

ROOT=Path(__file__).resolve().parents[1]


class BotUIControls(unittest.TestCase):
    def test_real_host_bot_event_projection_is_separate_from_public_progress_data(self):
        node=shutil.which('node');self.assertIsNotNone(node,'Node required for native Bot surface acceptance')
        process=subprocess.run([node,str(ROOT/'tests/bot_controls.cjs'),str(ROOT)],capture_output=True,text=True,timeout=10)
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        report=json.loads(process.stdout);self.assertEqual(report['status'],'passed');self.assertTrue(report['hostIdentitiesPreserved']);self.assertTrue(report['publicDataCannotApplyContext'])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class BotUI(unittest.TestCase):
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def setUp(self):
        registry=Registry();self.actions=[]
        @registry.tool('fixture.bot_action')
        def action(value,context):context.checkpoint();self.actions.append(value);return value
        class FixtureFramework(Framework):
            def __init__(self,*args,**kwargs):super().__init__(*args,registry=registry,**kwargs)
            def create_project(self,*args,**kwargs):
                kwargs['capabilities']=['fixture.bot_action']
                return super().create_project(*args,**kwargs)
        with patch.object(workbench,'Framework',FixtureFramework):workbench.WorkbenchUI.setUp(self)

    @hold_bot_plan_until_guide()
    def test_native_bot_actual_revision_actions_and_goal_in_existing_mobile_workbench(self):
        source='async fn plan(s){await progress_emit("plan",{summary:"原生 Bot 正在规划"});await sleep(0.35);if length(s.guidance)>0{s.guidance[-1].input.text}else{"old proposal"}};fn execute(p,s){tool("fixture.bot_action",p)};fn verify(s){{done:true,context:s.context,output:s.evidence}};let worker=bot({objective:"持续目标"},plan,execute,verify,context={kept:true});let result=await worker(input);await sleep(0.15);result'
        self.draft(source);self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        saved=self.app.get_project('workbench');self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>busy&&activeRunId&&document.querySelector("#eventsPanel").textContent.includes("原生 Bot 正在规划")')
        self.command('run.guide');self.draft(source+'; // unsaved draft retained')
        text='请验证新的目标 👋 <script>window.botInjected=true</script>'
        self.page.locator('#runGuidanceInput').fill(text);self.page.locator('#runGuidanceSend').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#eventsPanel").textContent.includes("Bot 上下文已应用")')
        run=self.app.get_run(self.page.evaluate('currentRun'));self.assertEqual(run['status'],'completed');self.assertEqual(run['version'],saved['version'])
        self.assertEqual(run['output']['status'],'achieved');self.assertEqual(run['output']['output'],[text]);self.assertEqual(run['output']['state']['goal'],{'objective':'持续目标'});self.assertEqual(run['output']['state']['context'],{'kept':True});self.assertEqual(self.actions,[text])
        self.assertTrue(self.page.evaluate('dirty'));self.assertIsNone(self.page.evaluate('window.botInjected'))
        events=self.app.events('workbench',run_id=run['id'],limit=300)['items'];applied=[e for e in events if e['type']=='bot.context.applied'];self.assertEqual(len(applied),1)
        queued=next(e for e in events if e['type']=='guidance.queued');received=next(e for e in events if e['type']=='guidance.received');self.assertLess(queued['id'],received['id']);self.assertLess(received['id'],applied[0]['id']);self.assertEqual(applied[0]['payload']['revision'],queued['payload']['revision'])
        admitted=[e for e in events if e['type']=='bot.action.admitted'];completed=[e for e in events if e['type']=='bot.action.completed'];self.assertTrue(admitted);self.assertTrue(completed)
        self.assertEqual({e['payload']['actionId']for e in admitted},{e['payload']['actionId']for e in completed})
        content=self.page.locator('#eventsPanel').text_content();self.assertIn('Bot 动作已准入',content);self.assertIn('Bot 动作有结果',content);self.assertIn('r1',content)
        self.page.set_viewport_size({'width':390,'height':844});self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
