"""Real local-browser acceptance for semantic typing assistance and formatting.

Setup may dispatch an initial CodeMirror document/selection, but automatic
completion assertions follow real keyboard.type calls without Ctrl-Space.
The composition case checks synthetic DOM contracts, not a real OS IME.
No providers, external HTTP, production storage, or inherited baseline tests.
"""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

from chaos import Framework, Settings
from chaos.program import parse_program
from chaos.server import make_server
from workbench_actions import view as workbench_view


LIBRARY = 'fn twice(value:Int)->Int{value*2};let constant=21;export {twice,constant}'
INITIAL = 'let original=42;original'


def _graph():
    kinds = [('input', 'input'), ('compute', 'function'), ('output', 'output')]
    return {
        'nodes': [{'id': name, 'kind': kind, 'label': name, 'x': i * 260,
                   'y': 180, 'config': {'source': INITIAL if name == 'compute' else 'input'}}
                  for i, (name, kind) in enumerate(kinds)],
        'edges': [{'id': a + '_' + b, 'source': a, 'target': b}
                  for a, b in [('input', 'compute'), ('compute', 'output')]],
    }


@unittest.skipUnless(importlib.util.find_spec('playwright'),
                     'optional Playwright extra unavailable')
class EditorAssistanceUI(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.tmp = tempfile.TemporaryDirectory(prefix='chaos-editor-assistance-')
        root = Path(self.tmp.name)
        self.app = Framework(root / 'runtime.sqlite', settings=Settings(':memory:'))
        self.app.create_project('assist', source=INITIAL,
                                files={'main.chaos': INITIAL, 'lib/math.chaos': LIBRARY},
                                capabilities=[])
        self.app.create_project('embedded', graph=_graph(),
                                files={'lib/math.chaos': LIBRARY}, capabilities=[])
        self.server = make_server('127.0.0.1', 0, framework=self.app,
                                  state_dir=root / 'control', update_home=root / 'updates')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        self.context = self.browser.new_context(viewport={'width': 1600, 'height': 1100})
        self.errors, self.external = [], []
        def local_only(route):
            if route.request.url.startswith(self.origin + '/'):
                route.continue_()
            else:
                self.external.append(route.request.url)
                route.abort()
        self.context.route('http**://**', local_only)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.origin + '/?project=assist')
        self.page.wait_for_function(
            '()=>window.ChaosWorkbench&&window.ChaosIDE?.editor()&&project?.id==="assist"')
        self.page.wait_for_function(
            '(source)=>ChaosIDE.editor().state.doc.toString()===source', arg=INITIAL)

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

    def _initial_document(self, source, *, cursor=None, embedded=False):
        self.page.keyboard.press('Escape')
        self.page.evaluate('''({source,cursor,embedded})=>{
          const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
          e.dispatch({changes:{from:0,to:e.state.doc.length,insert:source},
                      selection:{anchor:cursor===null?source.length:cursor}});
          e.focus();
        }''', {'source': source, 'cursor': cursor, 'embedded': embedded})

    def _source(self, *, embedded=False):
        return self.page.evaluate('''embedded=>{
          const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
          return e.state.doc.toString();
        }''', embedded)

    def _choice(self, label, *, embedded=False):
        scope = self.page.locator('.embedded-editor') if embedded else self.page.locator('#sourceView')
        return scope.locator('.cm-tooltip-autocomplete .cm-completionLabel').filter(
            has_text=re.compile('^' + re.escape(label) + '$')).first

    def _accept(self, label, *, embedded=False):
        item = self._choice(label, embedded=embedded)
        item.wait_for(state='visible')
        item.click()

    def _embedded(self):
        self.page.goto(self.origin + '/?project=embedded')
        workbench_view(self.page, 'graph')
        self.page.locator('[data-node="compute"]').click()
        self.page.locator('.embedded-editor .cm-content').wait_for(state='visible')
        self.page.wait_for_function(
            '()=>!!ChaosEditor.get(document.querySelector("textarea.source-field"))')
        return self.page.locator('.embedded-editor')

    def test_main_real_typing_symbols_then_fresh_module_member(self):
        self.assertTrue(self.page.locator('#ideSuggestButton').is_visible())
        self._initial_document('let answer=42;')
        self.page.keyboard.type('ans')
        self._accept('answer')
        self.assertEqual(self._source(), 'let answer=42;answer')
        # The same open menu must leave the lexical namespace on a typed dot.
        prefix = 'import "lib/math.chaos" as Math;let mySecret=7;Ma'
        self._initial_document(prefix)
        self.page.keyboard.type('th')
        self._choice('Math').wait_for(state='visible')
        self.page.keyboard.type('.')
        self._choice('twice').wait_for(state='visible')
        self.assertEqual(self._choice('mySecret').count(), 0)
        self._accept('twice')
        self.assertEqual(self._source(), prefix + 'th.twice')
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self.app.get_project('assist')['files']['main.chaos'], INITIAL)

    def test_main_middle_identifier_and_visible_explicit_control_use_full_range(self):
        source = 'let answer=42;ans_suffix'
        self._initial_document(source, cursor=source.rindex('ans_suffix') + 3)
        self.page.keyboard.type('w')
        self._accept('answer')
        self.assertEqual(self._source(), 'let answer=42;answer')
        self.assertNotIn('_suffix', self._source())
        # This separate explicit case uses the visible UI, not a private completer.
        self._initial_document('let answer=42;ans')
        self.page.locator('#ideSuggestButton').click()
        self._accept('answer')
        self.assertEqual(self._source(), 'let answer=42;answer')
        self.assertTrue(self.page.evaluate('dirty'))
        self.assertEqual(self.page.evaluate('project.version'), 1)

    def test_main_quoted_import_and_keyword_argument_typing_use_backend_ranges(self):
        source = 'import "lib/unused.chaos"'
        self._initial_document(source, cursor=len('import "lib/'))
        self.page.keyboard.type('ma')
        self._accept('lib/math.chaos')
        self.assertEqual(self._source(), 'import "lib/math.chaos"')
        prefix = 'fn collect(a:Int,b=2,*rest,required,**options)->Record{{}};collect(1,'
        self._initial_document(prefix)
        self.page.keyboard.type('req')
        self._accept('required=')
        self.assertEqual(self._source(), prefix + 'required=')
        self.assertEqual(self.app.get_project('assist')['version'], 1)

    def test_main_visible_default_preference_persists_and_ctrl_s_formats_active_module(self):
        preference = self.page.locator('#formatOnSave')
        self.assertTrue(preference.is_visible())
        self.assertTrue(preference.is_checked())
        self.page.evaluate('ChaosIDE.switchFile("lib/math.chaos")')
        self.page.wait_for_function('()=>activeFile==="lib/math.chaos"')
        raw = 'fn twice(value:Int)->Int{value*2};let constant=21;export {twice,constant}'
        self._initial_document(raw + '; // 保存当前模块\n')
        before = self._source()
        expected = self.app.format_source(before, file='lib/math.chaos')
        self.page.keyboard.press('ControlOrMeta+S')
        self.page.wait_for_function('()=>!dirty&&!saveTask&&project.version>1')
        saved = self.app.get_project('assist')
        self.assertEqual(saved['files']['lib/math.chaos'], expected)
        self.assertEqual(saved['files']['main.chaos'], INITIAL)
        self.assertEqual(parse_program(before)['semanticHash'], parse_program(expected)['semanticHash'])
        first_version = saved['version']
        preference.uncheck()
        self.assertEqual(self.page.evaluate('localStorage.getItem("chaos.ide.formatOnSave")'), 'false')
        unformatted = 'fn twice(value:Int)->Int{value*3};export {twice}'
        self._initial_document(unformatted)
        self.page.keyboard.press('ControlOrMeta+S')
        self.page.wait_for_function('(version)=>!dirty&&!saveTask&&project.version>version', arg=first_version)
        self.assertEqual(self.app.get_project('assist')['files']['lib/math.chaos'], unformatted)
        self.page.reload()
        self.page.wait_for_function('()=>!!ChaosIDE?.editor()&&project?.id==="assist"')
        self.assertTrue(self.page.locator('#formatOnSave').is_visible())
        self.assertFalse(self.page.locator('#formatOnSave').is_checked())
        self.assertEqual(self.app.get_project('assist')['files']['main.chaos'], INITIAL)

    def test_main_held_analysis_cannot_publish_into_another_active_file(self):
        from playwright.sync_api import Error
        old_source = 'let lateSymbol=42;lateSym'
        held = []
        def hold_old(route):
            body = route.request.post_data_json
            if body.get('source') == old_source:
                held.append(route)
            else:
                route.continue_()
        self.page.route('**/api/language/analyze', hold_old)
        self._initial_document('let lateSymbol=42;late')
        with self.page.expect_request(lambda request:
                request.method == 'POST' and '/api/language/analyze' in request.url
                and request.post_data_json.get('source') == old_source):
            self.page.keyboard.type('Sym')
        self.page.evaluate('ChaosIDE.switchFile("lib/math.chaos")')
        self.page.wait_for_function('()=>activeFile==="lib/math.chaos"')
        self._initial_document('let freshValue=7;')
        self.assertTrue(held, 'real old-file analysis must be pending before switching')
        response = self.app.analyze(old_source, files={'lib/math.chaos': LIBRARY},
                                    position=len(old_source))
        self.assertIn('lateSymbol', {item['label'] for item in response['completions']})
        released = 0
        for pending in held:
            try:
                pending.fulfill(status=200, content_type='application/json', body=json.dumps(response))
            except Error:
                # Aborting an old request is also a valid stale-response guard.
                pass
            released += 1
        self.assertGreater(released, 0)
        self.page.unroute('**/api/language/analyze', hold_old)
        self.page.keyboard.type('freshV')
        self._choice('freshValue').wait_for(state='visible')
        self.assertEqual(self._choice('lateSymbol').count(), 0)
        self._accept('freshValue')
        self.assertEqual(self._source(), 'let freshValue=7;freshValue')
        self.assertEqual(self.page.evaluate('activeFile'), 'lib/math.chaos')
        persisted = self.app.get_project('assist')
        self.assertEqual(persisted['files']['lib/math.chaos'], LIBRARY)
        self.assertEqual(persisted['files']['main.chaos'], INITIAL)
        self.assertEqual(persisted['version'], 1)

    def test_main_synthetic_composition_closes_menu_and_preserves_input(self):
        self._initial_document('let answer=42;')
        self.page.keyboard.type('ans')
        self._choice('answer').wait_for(state='visible')
        before = self._source()
        content = self.page.locator('#sourceView .cm-content')
        content.evaluate('el=>el.dispatchEvent(new CompositionEvent("compositionstart",{bubbles:true,data:""}))')
        self.page.wait_for_function('()=>ChaosIDE.editor().compositionStarted')
        self.page.locator('#sourceView .cm-tooltip-autocomplete').wait_for(state='hidden')
        # Synthetic event contract only: no real IME or OS-level claim.
        content.evaluate('''el=>el.dispatchEvent(new KeyboardEvent('keydown',{
          key:' ',code:'Space',ctrlKey:true,isComposing:true,bubbles:true,cancelable:true}))''')
        self.assertEqual(self._source(), before)
        self.assertEqual(self.page.locator('#sourceView .cm-tooltip-autocomplete').count(), 0)
        content.evaluate('el=>el.dispatchEvent(new CompositionEvent("compositionend",{bubbles:true,data:""}))')
        self.page.wait_for_function('()=>!ChaosIDE.editor().compositionStarted')
        self.page.keyboard.type('w')
        self._accept('answer')
        self.assertEqual(self._source(), 'let answer=42;answer')
        self.assertEqual(self.app.get_project('assist')['version'], 1)

    def test_embedded_real_typing_symbols_then_fresh_module_member(self):
        editor = self._embedded()
        self.assertTrue(editor.get_by_role('button', name='智能提示', exact=True).is_visible())
        self.assertTrue(editor.get_by_label('保存时格式化', exact=True).is_checked())
        self._initial_document('let answer=42;', embedded=True)
        self.page.keyboard.type('ans')
        self._accept('answer', embedded=True)
        self.assertEqual(self._source(embedded=True), 'let answer=42;answer')
        prefix = 'import "lib/math.chaos" as Math;let localOnly=1;Ma'
        self._initial_document(prefix, embedded=True)
        self.page.keyboard.type('th')
        self._choice('Math', embedded=True).wait_for(state='visible')
        self.page.keyboard.type('.')
        self._choice('constant', embedded=True).wait_for(state='visible')
        self.assertEqual(self._choice('localOnly', embedded=True).count(), 0)
        self._accept('constant', embedded=True)
        self.assertEqual(self._source(embedded=True), prefix + 'th.constant')
        self.assertEqual(self.page.locator('textarea.source-field').input_value(), self._source(embedded=True))
        self.assertEqual(self.app.get_project('embedded')['version'], 1)
        self.assertEqual(next(node['config']['source'] for node in self.app.get_project('embedded')['graph']['nodes']
                              if node['id'] == 'compute'), INITIAL)

    def test_embedded_middle_identifier_visible_control_and_readonly(self):
        editor = self._embedded()
        source = 'let answer=42;ans_suffix'
        self._initial_document(source, cursor=source.rindex('ans_suffix') + 3, embedded=True)
        self.page.keyboard.type('w')
        self._accept('answer', embedded=True)
        self.assertEqual(self._source(embedded=True), 'let answer=42;answer')
        self._initial_document('let answer=42;ans', embedded=True)
        editor.get_by_role('button', name='智能提示', exact=True).click()
        self._accept('answer', embedded=True)
        self.assertEqual(self._source(embedded=True), 'let answer=42;answer')
        # A disabled native textarea remains authoritative for the CM instance.
        self.page.evaluate('''()=>{
          const t=document.querySelector('textarea.source-field');t.disabled=true;
          ChaosEditor.get(t).syncDisabled();
        }''')
        self.assertTrue(editor.get_by_role('button', name='智能提示', exact=True).is_disabled())
        self.assertEqual(editor.locator('.cm-content').get_attribute('contenteditable'), 'false')
        self.assertEqual(editor.locator('.cm-tooltip-autocomplete').count(), 0)
        self.assertEqual(self.app.get_project('embedded')['version'], 1)


if __name__ == '__main__':
    unittest.main()
