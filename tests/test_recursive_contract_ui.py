"""Recursive contract programs use the unchanged source-first workbench."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from tests import test_workbench_ui as workbench
from tests.test_recursive_contract_surfaces import SOURCE


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class RecursiveContractUI(unittest.TestCase):
    setUp=workbench.WorkbenchUI.setUp
    tearDown=workbench.WorkbenchUI.tearDown
    command=workbench.WorkbenchUI.command
    draft=workbench.WorkbenchUI.draft
    state=workbench.WorkbenchUI.state

    def test_recursive_hint_format_save_run_and_http_sync(self):
        self.assertTrue(self.page.locator('#sourceView').is_visible())
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.draft('contract_recu')
        self.page.keyboard.press('End');self.page.keyboard.press('Control+Space')
        self.page.locator('.cm-completionLabel').filter(has_text='contract_recursive').click()
        self.assertTrue(self.page.evaluate('ChaosIDE.editor().state.doc.toString()').startswith('contract_recursive'))
        self.draft('contract_recursive({Node:contract_list(contract_ref("Node"))},')
        self.page.keyboard.press('End');self.command('edit.signature')
        self.page.wait_for_function('()=>document.querySelector("#ideSignatureHelp .active")?.textContent.includes("root")')
        self.assertIn('root: String',self.page.locator('#ideSignatureHelp').text_content())
        self.draft(SOURCE);self.command('edit.format')
        self.page.wait_for_function('()=>ChaosIDE.editor().state.doc.lines>5')
        self.page.locator('#saveButton').click();self.page.wait_for_function('()=>!dirty&&!saveTask')
        saved=self.app.get_project('workbench')
        self.page.wait_for_function('()=>document.querySelector("#ideLanguageStatus")?.textContent.includes("分析已更新")')
        self.page.locator('.cm-content').press('Control+Home')
        self.page.wait_for_function('()=>[...document.querySelectorAll(".ide-token-function")].map(el=>el.textContent).join("").includes("contract_recursive")')
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&document.querySelector("#resultPanel")?.textContent.trim()==="42"')
        self.assertTrue(self.page.locator('#resultPanel').is_visible());self.assertEqual(self.page.evaluate('runVersion'),saved['version'])
        changed=saved['source'].replace('value: 40','value: 41')
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
        self.page.locator('.cm-content').press('Control+Home')
        self.page.wait_for_function('()=>[...document.querySelectorAll(".ide-token-function")].map(el=>el.textContent).join("").includes("contract_recursive")')
        self.assertFalse(self.page.locator('#ideAssistantPanel').is_visible())
        self.assertEqual(self.errors,[]);self.assertEqual(self.external,[])
        output=os.environ.get('CHAOS_RECURSIVE_CONTRACT_AUDIT')
        if output:
            directory=Path(output);directory.mkdir(parents=True,exist_ok=True)
            name=self._testMethodName+'.png';path=directory/name;self.page.screenshot(path=str(path),full_page=True)
            report={'passed':True,'test':self.id(),'checks':[
                'same compact workbench and closed assistant',
                'native contract_recursive completion and root signature',
                'real command palette formatting, native helper highlighting and saved recursive-contract run result 42',
                'HTTP source update runs result 43 and survives reload'],
                'screenshots':{name:hashlib.sha256(path.read_bytes()).hexdigest()},'errors':self.errors,'externalHTTP':self.external}
            (directory/(self._testMethodName+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':unittest.main()
