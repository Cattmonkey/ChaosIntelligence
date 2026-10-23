"""The existing compact workbench edits and runs actual value dispatch."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from tests import test_workbench_ui as workbench
from tests.test_generic_eql_surfaces import SOURCE


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class GenericEqlUI(unittest.TestCase):
    setUp=workbench.WorkbenchUI.setUp
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def test_native_completion_signature_format_save_and_run_in_workbench(self):
        self.assertTrue(self.page.locator('#sourceView').is_visible())
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.draft('generic_eql_info(generic_eql(40)).')
        self.page.keyboard.press('End')
        self.page.keyboard.press('Control+Space')
        self.page.locator('.cm-completionLabel').filter(has_text='valueType').click()
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'),'generic_eql_info(generic_eql(40)).valueType')
        self.draft('generic_eql(')
        self.page.keyboard.press('End')
        self.command('edit.signature')
        self.page.wait_for_function('()=>document.querySelector("#ideSignatureHelp")?.textContent.includes("value: Any")')
        self.draft(SOURCE)
        self.command('edit.format')
        self.page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>4')
        self.page.locator('#saveButton').click()
        self.page.wait_for_function('()=>!dirty&&!saveTask')
        saved=self.app.get_project('workbench')
        self.assertGreater(saved['version'],1)
        self.assertIn('generic_eql(40)',saved['source'])
        self.page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
        self.assertGreater(self.page.locator('.ide-token-function').count(),0)
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()==="42"')
        self.assertTrue(self.page.locator('#resultPanel').is_visible())
        self.assertEqual(self.page.evaluate('runVersion'),saved['version'])
        self.assertEqual(self.page.locator('#wbOutputToggle').get_attribute('aria-expanded'),'true')
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        output=os.environ.get('CHAOS_EQL_AUDIT')
        if output:
            root=Path(output);root.mkdir(parents=True,exist_ok=True)
            name=self._testMethodName+'.png';path=root/name
            self.page.screenshot(path=str(path),full_page=True)
            report={'passed':True,'test':self.id(),'checks':[
                'unchanged compact source-first workbench and closed assistant',
                'native diagnostic field completion inserts valueType',
                'real command palette displays generic_eql value argument signature',
                'visible format/save/run preserve actual method selector and saved version',
                'language highlighting active; runtime result 42 visible without model calls'],
                'screenshots':{name:hashlib.sha256(path.read_bytes()).hexdigest()},
                'errors':self.errors,'externalHTTP':self.external}
            (root/(self._testMethodName+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':unittest.main()
