"""Visible workbench navigation delegates to the existing versioned IDE.

CHAOS_WORKBENCH_AUDIT optionally records per-case screenshots and evidence;
normal test runs write only into temporary runtime directories.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from chaos import Framework, Settings
from chaos.server import make_server


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class WorkbenchUI(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.tmp = tempfile.TemporaryDirectory(prefix='chaos-workbench-test-')
        root = Path(self.tmp.name)
        self.app = Framework(root/'runtime.sqlite', settings=Settings(':memory:'))
        self.source = 'fn add(left:Int,right:Int)->Int{left+right};add(20,22)'
        self.app.create_project('workbench', source=self.source, files={
            'main.chaos': self.source, 'lib/util.chaos': 'let answer=42;export {answer};'
        }, capabilities=[])
        self.server = make_server('127.0.0.1', 0, framework=self.app,
                                  state_dir=root/'control', update_home=root/'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(viewport={'width':1440, 'height':1000})
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.errors, self.external, self.checks, self.screenshots = [], [], [], {}
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.context.route('http**://**', lambda route: route.continue_()
                           if route.request.url.startswith(self.origin + '/')
                           else (self.external.append(route.request.url), route.abort()))
        self.page.goto(self.origin + '/?project=workbench')
        self.page.wait_for_function('()=>window.ChaosWorkbench&&window.ChaosIDE?.editor()&&project?.id==="workbench"')
        self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source', arg=self.source)

    def tearDown(self):
        try:
            self.assertEqual(self.errors, [])
            self.assertEqual(self.external, [])
        finally:
            self.browser.close()
            self.pw.stop()
            self.server.shutdown()
            self.thread.join(3)
            self.server.server_close()
            self.app.close()
            self.tmp.cleanup()

    def command(self, command):
        # The command is selected through its accessible visible palette, never a hidden button.
        self.page.keyboard.press('ControlOrMeta+Shift+P')
        self.page.locator('#wbCommandInput').fill(command)
        label = self.page.evaluate('(id)=>ChaosWorkbench.commands.find(c=>c.id===id).label', command)
        self.page.locator('#wbCommandList [role="option"]').filter(has_text=label).click()
        self.page.locator('#wbCommands').wait_for(state='hidden')

    def draft(self, source):
        content = self.page.locator('.cm-content[contenteditable="true"]')
        content.click()
        self.page.keyboard.press('ControlOrMeta+A')
        self.page.keyboard.insert_text(source)
        self.page.keyboard.press('Home')
        self.page.keyboard.press('Shift+ArrowRight')
        return self.state()

    def state(self):
        return self.page.evaluate('({source:ChaosIDE.editor().state.doc.toString(),selection:ChaosIDE.editor().state.selection.toJSON(),file:activeFile,version:project.version,dirty})')

    def shot(self, name):
        output = os.environ.get('CHAOS_WORKBENCH_AUDIT')
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            filename = self._testMethodName + '-' + name + '.png'
            path = directory / filename
            self.page.screenshot(path=str(path), full_page=True)
            self.screenshots[filename] = hashlib.sha256(path.read_bytes()).hexdigest()

    def finish(self, *checks):
        self.checks.extend(checks)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])
        output = os.environ.get('CHAOS_WORKBENCH_AUDIT')
        if output:
            directory = Path(output)
            directory.mkdir(parents=True, exist_ok=True)
            (directory/(self._testMethodName+'.json')).write_text(json.dumps({
                'passed':True, 'test':self.id(), 'checks':self.checks,
                'screenshots':self.screenshots, 'errors':self.errors,
                'externalHTTP':self.external,
            }, ensure_ascii=False, indent=2)+'\n')

    def test_source_first_defaults_and_registry_reachability(self):
        self.assertTrue(self.page.locator('#sourceView').is_visible())
        self.assertTrue(self.page.locator('#ideFilesPanel').is_visible())
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.assertFalse(self.page.locator('#wbRunPanel').is_visible())
        self.assertEqual(self.page.locator('#wbOutputToggle').get_attribute('aria-expanded'), 'false')
        self.assertGreater(self.page.locator('.ide-editor').bounding_box()['width'], 1000)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        required = {'files','file.quickOpen','search','run.options','run.start','run.stop',
                    'agent','graph','view.source','view.structure','view.proof','debug',
                    'team','program.candidate','processes','sessions','computers','modes',
                    'settings','docs','project.import','project.export','project.backup',
                    'run.snapshot','output.events','output.result','output.artifact','output.history'}
        commands = self.page.evaluate('ChaosWorkbench.commands.map(c=>({id:c.id,label:c.label,target:c.target||null}))')
        self.assertEqual(len(commands), len({item['id'] for item in commands}))
        self.assertTrue(required <= {item['id'] for item in commands})
        self.assertEqual(self.page.evaluate('ChaosWorkbench.commands.filter(c=>c.target&&!document.getElementById(c.target)).map(c=>c.id)'), [])
        self.page.locator('#wbCommandButton').click()
        self.page.locator('#wbCommandInput').fill('run.stop')
        self.assertEqual(self.page.locator('#wbCommandList [role="option"]').get_attribute('aria-disabled'), 'true')
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#wbCommandButton').evaluate('e=>e===document.activeElement'))
        self.assertEqual(self.app.get_project('workbench')['version'], 1)
        self.shot('desktop')
        self.finish('source/files defaults; assistant and output closed', 'all advanced actions registered with live targets',
                    'disabled stop remains unavailable', 'Escape restores launcher focus; no project write')

    def test_keyboard_palette_and_quick_files_preserve_drafts(self):
        before = self.draft('fn add(left:Int,right:Int)->Int{left+right};let draft=add(20,22);draft')
        self.page.keyboard.press('ControlOrMeta+Shift+P')
        self.assertTrue(self.page.locator('#wbCommandInput').evaluate('e=>e===document.activeElement'))
        self.page.locator('#wbCommandInput').fill('output.')
        first = self.page.locator('#wbCommandInput').get_attribute('aria-activedescendant')
        self.page.keyboard.press('ArrowDown')
        self.assertNotEqual(self.page.locator('#wbCommandInput').get_attribute('aria-activedescendant'), first)
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.evaluate('ChaosIDE.editor().hasFocus'))
        self.assertEqual(self.state(), before)
        self.command('output.toggle')
        self.assertEqual(self.state(), before)
        self.page.keyboard.press('ControlOrMeta+P')
        self.page.locator('#wbCommandInput').fill('lib/util.chaos')
        self.page.keyboard.press('Enter')
        self.page.wait_for_function('()=>activeFile==="lib/util.chaos"')
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'), 'let answer=42;export {answer};')
        self.page.keyboard.press('ControlOrMeta+P')
        self.page.locator('#wbCommandInput').fill('main.chaos')
        self.page.keyboard.press('Enter')
        self.page.wait_for_function('()=>activeFile==="main.chaos"')
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'), before['source'])
        self.assertTrue(self.page.evaluate('dirty'))
        self.assertEqual(self.app.get_project('workbench')['source'], self.source)
        self.shot('draft')
        self.finish('palette keyboard selection and Escape focus', 'pane toggle preserves source/selection/version',
                    'quick file navigation preserves unsaved drafts; no save')

    def test_advanced_dialogs_and_views_remain_reachable(self):
        before = self.state()
        for command, selector in [('team','#teamAuthorDialog'), ('program.candidate','.program-candidate-dialog'),
                                  ('processes','.process-dialog'), ('sessions','.session-dialog'),
                                  ('computers','.computer-dialog')]:
            self.command(command)
            self.page.locator(selector).wait_for(state='visible')
            self.assertTrue(self.page.locator(selector).evaluate('e=>e.open'))
            self.shot(command.replace('.', '-'))
            self.page.keyboard.press('Escape')
            self.page.locator(selector).wait_for(state='hidden')
        for command, selector in [('graph','#graphView'), ('view.structure','#structureView'),
                                  ('view.proof','#proofView'), ('view.source','#sourceView')]:
            self.command(command)
            self.assertTrue(self.page.locator(selector).is_visible())
        self.command('modes')
        self.assertTrue(self.page.locator('#modesPanel').is_visible())
        self.command('modes')
        self.assertFalse(self.page.locator('#modesPanel').is_visible())
        self.assertEqual(self.state(), before)
        self.assertEqual(self.app.get_project('workbench')['version'], 1)
        self.finish('Team/Program/Process/Session/Computer actual dialogs reachable',
                    'graph/IR/proof/source and modes reachable', 'navigation leaves saved source and version unchanged')

    def test_run_input_format_save_and_visible_output(self):
        self.draft('input.value+2')
        self.command('run.options')
        self.assertTrue(self.page.locator('#taskInput').is_visible())
        self.page.locator('#jsonInput').check()
        self.page.locator('#taskInput').fill('{"value":40}')
        self.command('run.close')
        self.command('edit.format')
        self.page.locator('#saveButton').click()
        self.page.wait_for_function('()=>!dirty&&!saveTask')
        saved = self.app.get_project('workbench')
        self.assertGreater(saved['version'], 1)
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel").textContent.trim()==="42"')
        self.assertTrue(self.page.locator('#resultPanel').is_visible())
        self.assertEqual(self.page.locator('#wbOutputToggle').get_attribute('aria-expanded'), 'true')
        self.assertEqual(self.page.evaluate('runVersion'), saved['version'])
        self.command('run.options')
        self.assertEqual(self.page.locator('#taskInput').input_value(), '{"value":40}')
        self.assertFalse(self.page.locator('#viewSnapshot').is_disabled())
        self.shot('input-result')
        self.finish('original JSON input semantics reach runtime', 'format/save/run version matches persisted source',
                    'result autoopens output; snapshot action enabled')

    def test_dynamic_registry_delegates_original_nodes_and_disabled_state(self):
        # Inject an ordinary DOM listener to count delegation without replacing the production handler.
        self.page.evaluate('()=>{window.workbenchClicks=0;const b=document.getElementById("compileSource");b.addEventListener("click",()=>workbenchClicks++);window.workbenchOriginal=b;}')
        self.command('edit.compile')
        self.page.locator('#compilationDialog').wait_for(state='visible')
        self.assertEqual(self.page.evaluate('workbenchClicks'), 1)
        self.assertTrue(self.page.evaluate('workbenchOriginal===document.getElementById("compileSource")'))
        self.page.locator('#compilationDialog').get_by_role('button', name='关闭', exact=True).click()
        self.page.evaluate('()=>document.getElementById("compileSource").disabled=true')
        self.page.keyboard.press('ControlOrMeta+Shift+P')
        self.page.locator('#wbCommandInput').fill('edit.compile')
        self.assertEqual(self.page.locator('#wbCommandList [role="option"]').get_attribute('aria-disabled'), 'true')
        self.page.keyboard.press('Enter')
        self.assertTrue(self.page.locator('#wbCommands').is_visible())
        self.assertEqual(self.page.evaluate('workbenchClicks'), 1)
        self.page.keyboard.press('Escape')
        self.shot('delegated')
        self.finish('command invokes original dynamically created compile control once',
                    'disabled target cannot execute through keyboard palette')

    def test_safe_presentation_preferences_and_mobile_palette(self):
        self.page.keyboard.press('ControlOrMeta+B')
        self.assertFalse(self.page.locator('#ideFilesPanel').is_visible())
        self.command('output.toggle')
        stored = self.page.evaluate('JSON.parse(localStorage.getItem("chaos.workbench.v1"))')
        self.assertEqual(stored, {'sidebar':False, 'output':True})
        self.page.reload()
        self.page.wait_for_function('()=>window.ChaosWorkbench&&window.ChaosIDE?.editor()')
        self.assertFalse(self.page.locator('#ideFilesPanel').is_visible())
        self.assertEqual(self.page.locator('#wbOutputToggle').get_attribute('aria-expanded'), 'true')
        self.page.set_viewport_size({'width':390,'height':900})
        self.command('output.toggle')
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertGreater(self.page.locator('.ide-editor').bounding_box()['width'], 300)
        self.page.locator('#wbActivity [data-command="files"]').click()
        self.assertTrue(self.page.locator('#ideFilesPanel').is_visible())
        self.page.locator('#wbActivity [data-command="files"]').click()
        self.assertFalse(self.page.locator('#ideFilesPanel').is_visible())
        self.page.locator('#wbCommandButton').click()
        self.page.locator('#wbCommandInput').fill('edit.signature')
        box = self.page.locator('#wbCommands').bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x']+box['width'], 390)
        self.shot('mobile-palette')
        self.page.keyboard.press('Escape')
        self.assertEqual(self.app.get_project('workbench')['version'], 1)
        self.finish('only sidebar/output boolean preferences persisted and restored',
                    '390px editor and files usable without horizontal overflow', 'mobile palette fits viewport')


if __name__ == '__main__':
    unittest.main()
