"""Real browser regressions for explicit Save versus internal save-before-run."""
import importlib.util
import unittest

import test_editor_assistance_ui as fixture
from workbench_actions import button, command, view


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class ExplicitSaveRoutingUI(unittest.TestCase):
    setUp = fixture.EditorAssistanceUI.setUp
    tearDown = fixture.EditorAssistanceUI.tearDown
    _source = fixture.EditorAssistanceUI._source

    def execute(self, expected):
        self.page.locator('#runButton').click()
        self.page.wait_for_function('()=>!busy&&!launching&&currentRun!==null')
        self.assertEqual(self.app.get_run(self.page.evaluate('currentRun'))['output'], expected)

    def test_clean_run_then_explicit_button_keyboard_and_menu_save(self):
        requests = []
        self.page.on('request', lambda request: requests.append(request.url)
                     if request.method == 'POST' and request.url.endswith('/api/language/format-source') else None)
        before = self.app.get_project('assist')
        self.execute(42)
        after = self.app.get_project('assist')
        self.assertEqual(after['version'], before['version'])
        self.assertEqual(after['source'], fixture.INITIAL)
        self.assertEqual(self._source(), fixture.INITIAL)
        self.assertEqual(requests, [])
        button(self.page, 'saveButton')
        self.page.wait_for_function('()=>!dirty&&!saveTask&&project.version===2')
        saved = self.app.get_project('assist')
        self.assertEqual(saved['source'], self.app.format_source(fixture.INITIAL))
        self.assertEqual(saved['version'], before['version']+1)
        self.page.keyboard.press('ControlOrMeta+S')
        self.page.wait_for_function('()=>!saveTask')
        command(self.page, 'file.save')
        self.page.wait_for_function('()=>!saveTask')
        self.assertEqual(self.app.get_project('assist')['version'], saved['version'])
        self.assertEqual(self._source(), saved['source'])

    def test_structure_save_then_run_keeps_revision_remote_cas_and_lines(self):
        from tests.test_completion_surfaces import SOURCE
        project = self.app.create_project('explicit-structure', source=SOURCE, capabilities=[])
        self.page.goto(self.origin+'/?project=explicit-structure')
        self.page.wait_for_function('()=>window.ChaosIDE?.editor()&&project?.id==="explicit-structure"')
        view(self.page, 'structure')
        self.page.wait_for_function('()=>programDraft?.root.statements[1].value.kind==="loop_else"')
        target = self.page.evaluate('programDraft.root.statements[1].value.otherwise.statements[0].otherwise.statements[0].arguments[0].id')
        self.page.locator('[data-syntax="'+target+'"]').click()
        field = self.page.locator('#syntaxFields .syntax-field').filter(has=self.page.locator('label', has_text='value'))
        field.locator('input').fill('42')
        field.get_by_role('button', name='应用', exact=True).click()
        button(self.page, 'applyProgram')
        self.page.wait_for_function('()=>!syntaxDirty&&!dirty&&!saveTask')
        saved = self.app.get_project(project['id'])
        view(self.page, 'source')
        self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source', arg=saved['source'])
        lines = self.page.evaluate('ChaosIDE.editor().state.doc.lines')
        self.execute(43)
        self.assertEqual(self.app.get_project(project['id'])['version'], saved['version'])
        self.assertEqual(self._source(), saved['source'])
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.lines'), lines)
        remote = saved['source'].replace('(add)(42)', '(add)(41)')
        self.assertNotEqual(remote, saved['source'])
        response = self.page.request.put(self.origin+'/api/v1/projects/'+project['id'], data={**saved,'source':remote,'files':{**saved['files'],saved['entry']:remote},'editFrom':'source'})
        self.assertTrue(response.ok, response.text())
        self.page.wait_for_function('(source)=>ChaosIDE.editor().state.doc.toString()===source&&!dirty', arg=remote)
        self.execute(42)
        self.assertEqual(self.app.get_project(project['id'])['version'], saved['version']+1)
        self.assertEqual(self._source(), remote)


if __name__ == '__main__':
    unittest.main()
