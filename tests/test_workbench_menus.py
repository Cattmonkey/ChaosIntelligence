"""Discoverable menus exercise original controllers in a disposable project."""
import importlib.util
import unittest
import test_workbench_ui as fixture

@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class WorkbenchMenus(unittest.TestCase):
    setUp = fixture.WorkbenchUI.setUp
    tearDown = fixture.WorkbenchUI.tearDown
    state = fixture.WorkbenchUI.state
    draft = fixture.WorkbenchUI.draft
    shot = fixture.WorkbenchUI.shot
    finish = fixture.WorkbenchUI.finish

    def menu(self, name, command):
        self.page.locator('#wbMenuHead-' + name).click()
        self.page.locator('#wbMenu-' + name + ' [data-menu-command="' + command + '"]').click()

    def test_new_file_entry_input_and_run_without_command_palette(self):
        self.assertEqual(self.page.locator('#wbMenuHeads > button').all_text_contents(),
                         ['文件','编辑','视图','运行','智能体','帮助'])
        self.assertTrue(self.page.locator('#formatSource').is_visible())
        self.assertTrue(self.page.locator('#checkSource').is_visible())
        self.menu('file', 'file.new')
        self.page.locator('#ideFilePath').fill('lib/answer.chaos')
        self.page.locator('#ideFileForm button[type="submit"]').click()
        self.page.wait_for_function('()=>activeFile==="lib/answer.chaos"')
        self.draft('input.value+2')
        self.menu('run', 'run.options')
        self.assertIn('main.chaos', self.page.locator('#wbRunEntry').inner_text())
        self.menu('file', 'file.entry')
        self.page.wait_for_function('()=>document.querySelector("#wbRunEntry").textContent.includes("lib/answer.chaos")')
        self.page.locator('#jsonInput').check()
        self.page.locator('#taskInput').fill('{"value":40}')
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel").textContent.trim()==="42"')
        self.assertTrue(self.page.locator('#resultPanel').is_visible())
        self.assertEqual(self.app.get_project('workbench')['entry'], 'lib/answer.chaos')
        self.assertEqual(self.page.evaluate('runVersion'), self.app.get_project('workbench')['version'])
        self.shot('entry-result')
        self.finish('visible menus create file/set entry/input/run without palette',
                    'open run panel updates entry immediately', 'result 42 from saved entry snapshot')

    def test_keyboard_disabled_toggle_and_draft_preservation(self):
        before = self.draft('let unsaved=40;unsaved+2')
        # A pointer click must switch open menus, without hover toggling it shut.
        self.page.locator('#wbMenuHead-file').click()
        self.page.locator('#wbMenuHead-edit').click()
        self.assertTrue(self.page.locator('#wbMenu-edit').is_visible())
        self.assertFalse(self.page.locator('#wbMenu-file').is_visible())
        self.page.locator('#wbMenuHead-run').click()
        self.assertTrue(self.page.locator('#wbMenu-run').is_visible())
        self.page.keyboard.press('Escape')
        self.page.keyboard.press('F10')
        self.assertTrue(self.page.locator('#wbMenuHead-file').evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('ArrowRight')
        self.page.keyboard.press('ArrowDown')
        self.assertTrue(self.page.locator('#wbMenu-edit [data-menu-command="search"]').evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('End')
        self.assertTrue(self.page.locator('#wbMenu-edit [data-menu-command="edit.compile"]').evaluate('e=>e===document.activeElement'))
        self.page.keyboard.press('Escape')
        self.assertTrue(self.page.locator('#wbMenuHead-edit').evaluate('e=>e===document.activeElement'))
        self.page.locator('#wbMenuHead-run').click()
        stop=self.page.locator('#wbMenu-run [data-menu-command="run.stop"]')
        self.assertEqual(stop.get_attribute('aria-disabled'), 'true')
        stop.press('Enter')
        self.assertTrue(self.page.locator('#wbMenu-run').is_visible())
        self.page.keyboard.press('Escape')
        self.page.locator('#wbMenuHead-edit').click()
        toggle=self.page.locator('#wbMenu-edit [data-menu-command="format.save"]')
        self.assertEqual(toggle.get_attribute('aria-checked'), 'true')
        toggle.click()
        self.assertFalse(self.page.locator('#formatOnSave').is_checked())
        self.page.locator('#wbMenuHead-edit').click()
        self.assertEqual(toggle.get_attribute('aria-checked'), 'false')
        self.page.keyboard.press('ArrowRight')
        self.assertTrue(self.page.locator('#wbMenu-view').is_visible())
        self.page.keyboard.press('Escape')
        self.assertEqual(self.state(), before)
        self.assertEqual(self.app.get_project('workbench')['version'], 1)
        self.shot('keyboard')
        self.finish('roving keyboard focus and Escape restore', 'disabled command does not execute',
                    'checkbox state delegates original control', 'source/selection/dirty/version preserved')

    def test_inline_controls_diagnostics_and_modal_focus(self):
        self.page.evaluate('()=>{window.originalFormat=document.getElementById("formatSource");window.formatClicks=0;originalFormat.addEventListener("click",()=>formatClicks++);}');
        self.page.locator('#formatSource').click()
        self.page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>2')
        self.assertEqual(self.page.evaluate('formatClicks'), 1)
        self.assertTrue(self.page.evaluate('originalFormat===document.querySelector("#wbEditorActions #formatSource")'))
        self.page.locator('#checkSource').click()
        self.page.wait_for_function('()=>document.querySelector("#ideLanguageStatus").textContent.includes("分析已更新")')
        self.assertEqual(self.page.locator('#ideDiagnostics').inner_text(), '未发现语法问题')
        self.menu('file', 'file.new')
        self.assertTrue(self.page.locator('#ideFileDialog').is_visible())
        self.assertTrue(self.page.locator('#ideFileDialog').evaluate('e=>e.contains(document.activeElement)'))
        self.assertFalse(self.page.locator('#wbMenu-file').is_visible())
        self.page.locator('#ideFileDismiss').click()
        self.draft('let =')
        self.page.locator('#checkSource').click()
        self.page.locator('#ideDiagnostics button').first.wait_for(state='visible')
        self.assertTrue(self.page.locator('#ideDiagnostics button').first.inner_text())
        self.assertEqual(self.app.get_project('workbench')['version'], 1)
        self.shot('diagnostic')
        self.finish('original format/check controls visible and execute once', 'dialogs retain focus',
                    'actual syntax errors remain visible and unsaved source is preserved')

    def test_compact_mobile_menus_and_reset_preserve_unsent_text(self):
        before=self.draft('let draft=42;draft')
        for width in (320,390,680,1440):
            self.page.set_viewport_size({'width':width,'height':900})
            self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            self.page.locator('#wbMenuHead-help').click()
            box=self.page.locator('#wbMenu-help').bounding_box()
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x']+box['width'], width)
            self.assertLessEqual(box['y']+box['height'], 900)
            self.shot('menu-'+str(width))
            self.page.keyboard.press('Escape')
        self.page.set_viewport_size({'width':390,'height':900})
        # The browser dispatches matchMedia's change after the resize command returns.
        self.page.wait_for_function("""()=>document.querySelector('#wbActivity [data-command="files"]').getAttribute('aria-pressed')==='false'""")
        self.assertEqual(self.page.locator('#wbActivity [data-command="files"]').get_attribute('aria-pressed'),'false')
        self.menu('agent','agent')
        self.page.locator('#ideChatPrompt').fill('这段需求尚未发送')
        self.menu('view','view.reset')
        self.assertTrue(self.page.locator('.ide-editor').is_visible())
        self.assertFalse(self.page.locator('#ideFilesPanel').is_visible())
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.assertEqual(self.page.locator('#ideChatPrompt').input_value(),'这段需求尚未发送')
        self.assertEqual(self.state(),before)
        self.assertEqual(self.app.get_project('workbench')['version'],1)
        self.shot('mobile-reset')
        self.finish('menus fit 320/390/680/1440 viewports', 'mobile files state truthful',
                    'reset restores compact editor without losing draft/selection/unsent assistant input')

if __name__ == '__main__':
    unittest.main()
