"""Actual save actions format clean source without inventing dirty revisions."""
import importlib.util
import unittest

from chaos.program import parse_program
import test_editor_assistance_ui as fixture
from workbench_actions import view as workbench_view


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class EditorCleanSaveUI(unittest.TestCase):
    setUp = fixture.EditorAssistanceUI.setUp
    tearDown = fixture.EditorAssistanceUI.tearDown
    _source = fixture.EditorAssistanceUI._source
    _initial_document = fixture.EditorAssistanceUI._initial_document

    def _save(self, keyboard=False):
        if keyboard:
            self.page.keyboard.press('ControlOrMeta+S')
        else:
            self.page.locator('#saveButton').click()
        self.page.wait_for_function('()=>!saveTask')

    def _format_requests(self):
        requests = []
        self.page.on('request', lambda request: requests.append(request.post_data_json)
                     if request.method == 'POST' and request.url.endswith('/api/language/format-source') else None)
        return requests

    def test_clean_active_module_save_formats_once_and_repeat_keeps_revision(self):
        self.page.evaluate('ChaosIDE.switchFile("lib/math.chaos")')
        self.page.wait_for_function('()=>activeFile==="lib/math.chaos"')
        self.assertFalse(self.page.evaluate('dirty'))
        requests = self._format_requests()
        self._save(keyboard=True)
        expected = self.app.format_source(fixture.LIBRARY, file='lib/math.chaos')
        saved = self.app.get_project('assist')
        self.assertEqual(saved['version'], 2)
        self.assertEqual(saved['files']['lib/math.chaos'], expected)
        self.assertEqual(saved['files']['main.chaos'], fixture.INITIAL)
        self.assertEqual(self._source(), expected)
        self.assertEqual(parse_program(fixture.LIBRARY)['semanticHash'], parse_program(expected)['semanticHash'])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['file'], 'lib/math.chaos')
        self._save()
        again = self.app.get_project('assist')
        self.assertEqual(again['version'], 2)
        self.assertEqual(again['files'], saved['files'])
        self.assertEqual(len(requests), 2)

    def test_disabled_preference_leaves_clean_source_and_revision_unchanged(self):
        requests = self._format_requests()
        self.page.locator('#formatOnSave').uncheck()
        self._save(keyboard=True)
        self.assertEqual(requests, [])
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self._source(), fixture.INITIAL)

    def test_clean_graph_and_structure_save_do_not_format_hidden_source(self):
        requests = self._format_requests()
        for mode in ('graph', 'structure'):
            workbench_view(self.page, mode)
            self.assertFalse(self.page.evaluate('dirty'))
            self._save()
            self.assertEqual(requests, [])
            self.assertEqual(self.app.get_project('assist')['version'], 1)
            self.assertEqual(self.app.get_project('assist')['files']['main.chaos'], fixture.INITIAL)

    def test_clean_readonly_and_snapshot_never_format_or_commit(self):
        requests = self._format_requests()
        # Exercise host read-only states independently of text edits.
        for setup, restore in (
            ('document.querySelector("#sourceEditor").readOnly=true', 'document.querySelector("#sourceEditor").readOnly=false'),
            ('snapshotView=true', 'snapshotView=false'),
        ):
            self.page.evaluate(setup)
            self._save()
            self.assertEqual(requests, [])
            self.assertEqual(self.app.get_project('assist')['version'], 1)
            self.assertEqual(self._source(), fixture.INITIAL)
            self.page.evaluate(restore)

    def test_known_remote_conflict_precedes_formatter_for_clean_and_dirty(self):
        requests = self._format_requests()
        self.page.evaluate('remoteProject={...project,version:project.version+1}')
        self._save()
        self.assertEqual(requests, [])
        self._initial_document('let local=42;local')
        self._save()
        self.assertEqual(requests, [])
        self.assertEqual(self._source(), 'let local=42;local')
        self.assertTrue(self.page.evaluate('dirty'))
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertIn('远端版本已更新', self.page.locator('#toast').text_content())

    def test_clean_composition_blocks_save_before_request_then_recovers(self):
        requests = self._format_requests()
        content = self.page.locator('#sourceView .cm-content')
        content.evaluate('el=>el.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true,data:""}))')
        self.page.wait_for_function('()=>ChaosIDE.editor().compositionStarted')
        self._save()
        self.assertEqual(requests, [])
        self.assertEqual(self._source(), fixture.INITIAL)
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertIn('输入法', self.page.locator('#toast').text_content())
        content.evaluate('el=>el.dispatchEvent(new CompositionEvent("compositionend",{bubbles:true,data:""}))')
        self.page.wait_for_function('()=>!ChaosIDE.editor().compositionStarted')
        self._save()
        self.assertEqual(self._source(), self.app.format_source(fixture.INITIAL))
        self.assertEqual(self.app.get_project('assist')['version'], 2)
        self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
