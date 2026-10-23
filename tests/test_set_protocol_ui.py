"""Protocol Set programs use the unchanged source-first workbench."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from tests import test_workbench_ui as workbench
from tests.test_set_protocol_surfaces import SOURCE
from tests.test_set_protocol_integration import KEY


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class SetProtocolUI(unittest.TestCase):
    setUp=workbench.WorkbenchUI.setUp
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def test_object_set_hint_format_save_run_and_http_sync(self):
        self.assertTrue(self.page.locator('#sourceView').is_visible())
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.draft('set_dis')
        self.page.keyboard.press('End');self.page.keyboard.press('Control+Space')
        self.page.locator('.cm-completionLabel').filter(has_text='set_discard').click()
        self.assertTrue(self.page.evaluate('ChaosIDE.editor().state.doc.toString()').startswith('set_discard'))
        self.draft(KEY+'let values:Set={Key(42)};set_add(values,')
        self.page.keyboard.press('End');self.command('edit.signature')
        self.page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("item")')
        self.assertIn('value: Set',self.page.locator('#ideSignatureHelp').text_content())
        self.draft(SOURCE);self.command('edit.format')
        self.page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>10')
        self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        saved=self.app.get_project('workbench')
        self.page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
        self.assertGreater(self.page.locator('.ide-token-type',has_text='Set').count(),0)
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()==="42"')
        self.assertTrue(self.page.locator('#resultPanel').is_visible());self.assertEqual(self.page.evaluate('runVersion'),saved['version'])
        changed=saved['source'].replace('let start = 20','let start = 21')
        self.assertNotEqual(changed,saved['source'])
        response=self.page.request.put(self.origin+'/api/v1/projects/workbench',data={**saved,'source':changed,'files':{**saved['files'],saved['entry']:changed},'editFrom':'source'})
        self.assertTrue(response.ok,response.text())
        self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source&&!dirty',arg=changed)
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()==="43"')
        self.page.reload();self.page.wait_for_function('(source)=>window.ChaosIDE?.editor()?.state.doc.toString()===source',arg=changed)
        self.page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
        self.page.locator('[data-console="result"]').click()
        self.page.wait_for_function('()=>document.querySelector("#resultPanel")?.textContent.trim()==="43"')
        self.assertGreater(self.page.locator('.ide-token-type',has_text='Set').count(),0)
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        output=os.environ.get('CHAOS_SET_PROTOCOL_AUDIT')
        if output:
            directory=Path(output);directory.mkdir(parents=True,exist_ok=True)
            name=self._testMethodName+'.png';path=directory/name;self.page.screenshot(path=str(path),full_page=True)
            report={'passed':True,'test':self.id(),'checks':[
                'same compact workbench and closed assistant',
                'native set_discard completion and set_add item signature',
                'real command palette formatting, Set highlighting and saved object Set run result 42',
                'HTTP source update runs result 43 and survives reload'],
                'screenshots':{name:hashlib.sha256(path.read_bytes()).hexdigest()},'errors':self.errors,'externalHTTP':self.external}
            (directory/(self._testMethodName+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':unittest.main()
