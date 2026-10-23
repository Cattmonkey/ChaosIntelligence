"""Actual backend roles and browser controls across shared code appearances."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import unittest

import test_editor_assistance_ui as fixture
import test_semantic_color_ui as color_fixture

ROOT = Path(__file__).resolve().parent.parent
SOURCE = color_fixture.SOURCE
MODES = ('dark', 'paper', 'contrast')


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class EditorAppearanceUI(unittest.TestCase):
    setUp = fixture.EditorAssistanceUI.setUp
    tearDown = fixture.EditorAssistanceUI.tearDown
    backend = color_fixture.SemanticColorUI.backend
    main = color_fixture.SemanticColorUI.main
    embedded = color_fixture.SemanticColorUI.embedded
    reader = color_fixture.SemanticColorUI.reader
    colors = color_fixture.SemanticColorUI.colors
    background = color_fixture.SemanticColorUI.background
    assert_readable = color_fixture.SemanticColorUI.assert_readable

    def mode(self, page, value):
        page.evaluate('id=>ChaosCodeAppearance.set(id)', value)
        page.wait_for_function('id=>ChaosCodeAppearance.get()===id&&document.documentElement.dataset.codePalette===id', arg=value)

    def facet(self, page, embedded, expected):
        page.wait_for_function('''({embedded,expected})=>{
          const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
          return e.state.facet(ChaosCM.EditorView.darkTheme)===expected;
        }''', arg={'embedded': embedded, 'expected': expected})

    def evidence(self, page, label, observed):
        folder = os.environ.get('CHAOS_APPEARANCE_AUDIT') or os.environ.get('CHAOS_EDITOR_APPEARANCE_AUDIT')
        if not folder:
            return
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / (label + '.png')), full_page=True)
        data = {'surface': label, 'observed': observed, 'errors': self.errors,
                'externalHTTP': self.external,
                'sourceHashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                 for name in ('web/theme.css', 'web/code.js', 'web/editor.js', 'web/ide.js')}}
        (target / (label + '.json')).write_text(json.dumps(data, indent=2) + '\n')

    def readable_control(self, locator, label):
        foreground = locator.evaluate('el=>getComputedStyle(el).color')
        ratio = color_fixture.contrast(foreground, self.background(locator))
        self.assertGreaterEqual(ratio, 4.5, (label, foreground, ratio))
        return ratio

    def test_three_modes_keep_real_roles_readable_and_equal_on_all_surfaces_and_print(self):
        analysis = self.backend(SOURCE)
        references = {}
        print_reference = None
        for name, prepare in (('main', self.main), ('embedded', self.embedded), ('reader', self.reader)):
            page, scope, prefix = prepare(SOURCE)
            body_background = page.locator('body').evaluate('el=>getComputedStyle(el).backgroundColor')
            for mode in MODES:
                self.mode(page, mode)
                if name != 'reader':
                    self.facet(page, name == 'embedded', mode != 'paper')
                actual = self.colors(page, scope, prefix, analysis, SOURCE)
                if mode in references:
                    self.assertEqual(actual, references[mode], (name, mode))
                else:
                    references[mode] = actual
                for role in ('function', 'parameter', 'type', 'property'):
                    self.assertNotEqual(actual[role], actual['variable'], (name, mode, role))
                normal = self.background(scope if name == 'reader' else scope.locator('.cm-editor'))
                measured = {'normal': self.assert_readable(actual, normal, name + mode)}
                active = scope.locator('[aria-current="step"]' if name == 'reader' else '.cm-activeLine').first
                active.wait_for(state='attached')
                measured['active'] = self.assert_readable(actual, self.background(active), name + mode + ' active')
                if name != 'reader':
                    page.evaluate('''embedded=>{
                      const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
                      e.dispatch({selection:{anchor:0,head:e.state.doc.length}});e.focus();
                    }''', name == 'embedded')
                    selection = scope.locator('.cm-selectionBackground').first
                    selection.wait_for(state='attached')
                    selected = color_fixture.over(color_fixture.rgba(selection.evaluate('el=>getComputedStyle(el).backgroundColor')), normal)
                    measured['selection'] = self.assert_readable(actual, selected, name + mode + ' selected')
                else:
                    span = scope.locator('.chaos-token-function').first
                    page.evaluate('''()=>{const range=document.createRange();range.selectNodeContents(document.querySelector('pre.chaos-code code'));const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}''')
                    selection_color = color_fixture.rgba(span.evaluate('el=>getComputedStyle(el,"::selection").backgroundColor'))
                    self.assertGreater(selection_color[3], 0, 'Reader selection needs an actual palette background')
                    selected = color_fixture.over(selection_color, normal)
                    measured['selection'] = self.assert_readable(actual, selected, name + mode + ' reader selection')
                self.assertEqual(page.locator('body').evaluate('el=>getComputedStyle(el).backgroundColor'), body_background)
                self.evidence(page, 'appearance-' + name + '-' + mode, {'colors': actual, 'contrast': measured})
                page.emulate_media(media='print')
                try:
                    printed = self.colors(page, scope, prefix, analysis, SOURCE)
                    self.assert_readable(printed, self.background(scope if name == 'reader' else scope.locator('.cm-editor')), name + mode + ' print')
                    if print_reference is None:
                        print_reference = printed
                    else:
                        self.assertEqual(printed, print_reference)
                finally:
                    page.emulate_media(media='screen')
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self.app.get_project('embedded')['version'], 1)

    def test_visible_control_legend_cached_modules_history_selection_and_composition(self):
        page = self.page
        button = page.locator('#codeAppearanceButton')
        self.assertTrue(button.is_visible())
        button.click()
        dialog = page.locator('#codeAppearanceDialog')
        dialog.wait_for(state='visible')
        self.assertEqual(dialog.locator('input[name="codeAppearance"]').count(), 3)
        dialog.locator('#codeAppearanceLegend summary').click()
        legend = dialog.locator('.code-appearance-role')
        self.assertEqual(legend.count(), 11)
        self.assertEqual(set(legend.evaluate_all('els=>els.map(el=>el.dataset.role)')), color_fixture.ROLES)
        dialog.locator('input[name="codeAppearance"][value="paper"]').check()
        page.keyboard.press('Escape')
        dialog.wait_for(state='hidden')
        self.assertTrue(button.evaluate('el=>el===document.activeElement'))
        self.facet(page, False, False)
        # The saved initial document has a clean history. A real typed edit must
        # remain one undo operation through appearance changes and cached states.
        page.evaluate('()=>{const e=ChaosIDE.editor();e.dispatch({selection:{anchor:e.state.doc.length}});e.focus()}')
        page.keyboard.type(' + 1')
        page.evaluate('()=>ChaosIDE.editor().dispatch({selection:{anchor:1,head:4}})')
        snapshot = page.evaluate('()=>{const e=ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}')
        self.mode(page, 'contrast')
        self.facet(page, False, True)
        self.assertEqual(page.evaluate('()=>{const e=ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}'), snapshot)
        content = page.locator('#sourceView .cm-content')
        content.dispatch_event('compositionstart', {'data': '中'})
        page.wait_for_function('()=>ChaosIDE.editor().compositionStarted')
        self.mode(page, 'paper')
        self.facet(page, False, False)
        self.assertTrue(page.evaluate('()=>!!ChaosIDE.editor().compositionStarted'))
        self.assertEqual(page.evaluate('()=>{const e=ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}'), snapshot)
        content.dispatch_event('compositionend', {'data': ''})
        page.wait_for_function('()=>!ChaosIDE.editor().compositionStarted')
        page.evaluate('()=>ChaosIDE.editor().focus()')
        page.keyboard.press('ControlOrMeta+z')
        self.assertEqual(page.evaluate('()=>ChaosIDE.editor().state.doc.toString()'), fixture.INITIAL)
        page.evaluate('ChaosIDE.switchFile("lib/math.chaos")')
        page.wait_for_function('()=>activeFile==="lib/math.chaos"')
        self.mode(page, 'contrast')
        self.facet(page, False, True)
        page.evaluate('ChaosIDE.switchFile("main.chaos")')
        page.wait_for_function('()=>activeFile==="main.chaos"')
        self.facet(page, False, True)
        self.assertEqual(page.evaluate('()=>ChaosIDE.editor().state.doc.toString()'), fixture.INITIAL)
        self.mode(page, 'paper')
        self.facet(page, False, False)
        page.evaluate('ChaosIDE.switchFile("lib/math.chaos")')
        page.wait_for_function('()=>activeFile==="lib/math.chaos"')
        self.facet(page, False, False)
        self.assertEqual(page.evaluate('()=>ChaosIDE.editor().state.doc.toString()'), fixture.LIBRARY)
        self.assertEqual(self.app.get_project('assist')['version'], 1)

    def test_preferences_persist_and_real_cross_page_storage_invalid_and_clear_synchronize(self):
        self.mode(self.page, 'paper')
        self.assertEqual(self.page.evaluate('localStorage.getItem("chaos.codeAppearance")'), 'paper')
        self.page.reload()
        self.page.wait_for_function('()=>window.ChaosIDE?.editor()&&ChaosCodeAppearance.get()==="paper"')
        self.facet(self.page, False, False)
        other = self.context.new_page()
        other.goto(self.origin + '/code.css')
        other.set_content('<!doctype html><main></main>')
        other.add_script_tag(url=self.origin + '/code.js')
        self.mode(other, 'contrast')
        self.page.wait_for_function('()=>ChaosCodeAppearance.get()==="contrast"')
        self.facet(self.page, False, True)
        other.evaluate('localStorage.setItem("chaos.codeAppearance","invalid-mode")')
        self.page.wait_for_function('()=>ChaosCodeAppearance.get()==="dark"')
        self.mode(other, 'paper')
        self.page.wait_for_function('()=>ChaosCodeAppearance.get()==="paper"')
        other.evaluate('localStorage.removeItem("chaos.codeAppearance")')
        self.page.wait_for_function('()=>ChaosCodeAppearance.get()==="dark"')
        self.assertEqual(self.page.evaluate('ChaosIDE.editor().state.doc.toString()'), fixture.INITIAL)
        self.assertEqual(self.app.get_project('assist')['version'], 1)

    def test_denied_storage_live_modes_and_actual_search_completion_hover_stay_readable(self):
        self.page.add_init_script('''{
          const get=Storage.prototype.getItem,set=Storage.prototype.setItem;
          Storage.prototype.getItem=function(key){if(key==='chaos.codeAppearance')throw new DOMException('Blocked','SecurityError');return get.call(this,key)};
          Storage.prototype.setItem=function(key,value){if(key==='chaos.codeAppearance')throw new DOMException('Blocked','SecurityError');return set.call(this,key,value)};
        }''')
        for name, prepare in (('main', self.main), ('embedded', self.embedded)):
            page, scope, prefix = prepare(SOURCE)
            self.assertEqual(page.evaluate('ChaosCodeAppearance.get()'), 'dark')
            for mode in MODES:
                self.mode(page, mode)
                self.facet(page, name == 'embedded', mode != 'paper')
                page.evaluate('''embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();e.dispatch({selection:{anchor:e.state.doc.length}});e.focus()}''', name == 'embedded')
                page.keyboard.press('Meta+f' if sys.platform == 'darwin' else 'Control+f')
                panel = scope.locator('.cm-search')
                panel.wait_for(state='visible')
                observed = {'search': self.readable_control(panel.locator('input[name="search"]'), name + mode + ' search'),
                            'button': self.readable_control(panel.locator('button[name="next"]'), name + mode + ' button')}
                panel.locator('button[name="close"]').click()
                page.evaluate('''embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();e.dispatch({selection:{anchor:e.state.doc.length}});e.focus()}''', name == 'embedded')
                suggest = scope.get_by_role('button', name='智能提示', exact=True)
                suggest.click()
                popup = scope.locator('.cm-tooltip-autocomplete')
                popup.wait_for(state='visible')
                observed['selected'] = self.readable_control(popup.locator('li[aria-selected="true"]'), name + mode + ' completion')
                page.keyboard.press('Escape')
                function = scope.locator('.' + prefix + 'function').filter(has_text=re.compile('^transform$')).last
                function.hover()
                tooltip = scope.locator('.cm-tooltip').filter(has=page.locator('.ide-hover,.embedded-editor-hover'))
                tooltip.wait_for(state='visible')
                observed['hover'] = self.readable_control(tooltip, name + mode + ' hover')
                self.evidence(page, 'appearance-chrome-' + name + '-' + mode, observed)
            self.assertEqual(page.evaluate('''embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();return e.state.doc.toString()}''', name == 'embedded'), SOURCE)
        self.page.reload()
        self.page.wait_for_function('()=>window.ChaosCodeAppearance?.get()==="dark"')
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self.app.get_project('embedded')['version'], 1)


if __name__ == '__main__':
    unittest.main()
