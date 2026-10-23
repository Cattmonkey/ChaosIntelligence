"""Real editor controls share the palette without changing language semantics.

Only temporary local Framework/browser fixtures are used. The standalone mount
loads code.css without page/editor CSS, exercising the shared theme foundation.
"""
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
SOURCE = ('fn chromaStep(value:Int)->Int{value+1};\n'
          'let chromaResult=chromaStep(41);\nchromaResult')


@unittest.skipUnless(importlib.util.find_spec('playwright'),
                     'optional Playwright extra unavailable')
class EditorChromeColors(unittest.TestCase):
    # Import fixture modules, never another TestCase class or its test methods.
    setUp = fixture.EditorAssistanceUI.setUp
    tearDown = fixture.EditorAssistanceUI.tearDown
    background = color_fixture.SemanticColorUI.background

    def prepare(self, name, source):
        if name == 'main':
            self.page.goto(self.origin + '/?project=assist')
            self.page.wait_for_function(
                '()=>window.ChaosIDE?.editor()&&project?.id==="assist"')
            fixture.EditorAssistanceUI._initial_document(self, source)
            return self.page, self.page.locator('#sourceView'), 'ide-token-'
        if name == 'embedded':
            box = fixture.EditorAssistanceUI._embedded(self)
            fixture.EditorAssistanceUI._initial_document(self, source, embedded=True)
            return self.page, box, 'chaos-token-'
        page = self.context.new_page()
        page.set_default_timeout(10000)
        page.on('pageerror', lambda error: self.errors.append(str(error)))
        page.goto(self.origin + '/code.css')
        page.set_content('<!doctype html><main><textarea id="draft"></textarea></main>')
        page.add_style_tag(url=self.origin + '/code.css')
        for script in ('vendor/codemirror.js', 'editor-assist.js', 'editor.js'):
            page.add_script_tag(url=self.origin + '/' + script)
        page.evaluate('''source=>{
          const area=document.querySelector('#draft');area.value=source;
          window.chromeEditor=ChaosEditor.mount(area,{
            identity:()=>"chrome-colors",
            context:()=>({file:"main.chaos",entry:"main.chaos"})
          });
          chromeEditor.editor.dispatch({selection:{anchor:source.length}});
          chromeEditor.editor.focus();
        }''', source)
        return page, page.locator('.embedded-editor'), 'chaos-token-'

    def expected(self, locator, variable):
        return locator.evaluate('''(el,name)=>{
          const probe=document.createElement('i');
          probe.style.color=getComputedStyle(el).getPropertyValue(name).trim();
          (el.parentElement||document.body).append(probe);
          const value=getComputedStyle(probe).color;probe.remove();
          return value;
        }''', variable)

    def readable(self, locator, background_variable, label):
        actual = locator.evaluate('''el=>{
          const style=getComputedStyle(el);
          return {color:style.color,background:style.backgroundColor};
        }''')
        self.assertEqual(actual['color'], self.expected(locator, '--text'), label)
        self.assertEqual(actual['background'], self.expected(locator, background_variable), label)
        actual['contrast'] = color_fixture.contrast(actual['color'], self.background(locator))
        self.assertGreaterEqual(actual['contrast'], 4.5, (label, actual))
        return actual

    def source(self, page, name):
        return page.evaluate('''name=>{
          const editor=name==='standalone'?chromeEditor.editor:name==='main'?
            ChaosIDE.editor():ChaosEditor.get(document.querySelector('textarea.source-field')).editor;
          return editor.state.doc.toString();
        }''', name)

    def evidence(self, page, label, observed):
        folder = os.environ.get('CHAOS_EDITOR_CHROME_AUDIT')
        if not folder:
            return
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / (label + '.png')), full_page=True)
        payload = {'status': 'passed', 'surface': label, 'observed': observed,
                   'errors': self.errors, 'externalHTTP': self.external,
                   'sourceHashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                    for name in ('web/theme.css', 'web/editor.css',
                                                 'web/editor.js', 'web/ide.js')}}
        (target / (label + '.json')).write_text(json.dumps(payload, indent=2) + '\n')

    def test_real_search_panels_and_controls_share_readable_colors_on_all_mounts(self):
        observed = {}
        for name in ('main', 'embedded', 'standalone'):
            page, scope, _ = self.prepare(name, SOURCE)
            page.keyboard.press('Meta+f' if sys.platform == 'darwin' else 'Control+f')
            panel = scope.locator('.cm-search')
            panel.wait_for(state='visible')
            panels = scope.locator('.cm-panels')
            search = panel.locator('input[name="search"]')
            search.fill('chroma')
            search.press('ArrowRight')
            replacement = panel.locator('input[name="replace"]')
            button = panel.locator('button[name="next"]')
            case = panel.locator('input[name="case"]')
            case.check()
            colors = {
                'panel': self.readable(panels, '--panel2', name + ' panel'),
                'search': self.readable(search, '--editor-bg', name + ' search'),
                'replace': self.readable(replacement, '--editor-bg', name + ' replace'),
                'button': self.readable(button, '--panel', name + ' button')}
            self.assertEqual(button.evaluate('el=>getComputedStyle(el).backgroundImage'), 'none')
            self.assertEqual(case.evaluate('el=>getComputedStyle(el).accentColor'),
                             self.expected(case, '--accent'))
            observed[name] = colors
            self.evidence(page, 'chrome-search-' + name, colors)
            panel.locator('button[name="close"]').click()
            self.assertEqual(self.source(page, name), SOURCE)
        self.assertEqual(observed['main'], observed['embedded'])
        self.assertEqual(observed['main'], observed['standalone'])
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self.app.get_project('embedded')['version'], 1)
        self.assertEqual(self.external, [])

    def test_real_completion_and_hover_share_readable_colors_on_all_mounts(self):
        observed = {}
        prefix = 'let chroma=1;let chromatic=2;'
        for name in ('main', 'embedded', 'standalone'):
            page, scope, token_prefix = self.prepare(name, prefix)
            page.keyboard.type('chr')
            popup = scope.locator('.cm-tooltip-autocomplete')
            popup.wait_for(state='visible')
            selected = popup.locator('li[aria-selected="true"]')
            selected.wait_for(state='visible')
            self.assertGreaterEqual(popup.locator('.cm-completionLabel').count(), 2)
            colors = {'completion': self.readable(popup, '--editor-tooltip', name + ' completion'),
                      'selected': self.readable(selected, '--editor-selection', name + ' selected')}
            self.evidence(page, 'chrome-completion-' + name, colors)
            page.keyboard.press('Escape')
            self.assertEqual(self.source(page, name), prefix + 'chr')
            page, scope, token_prefix = self.prepare(name, SOURCE)
            function = scope.locator('.' + token_prefix + 'function').filter(
                has_text=re.compile('^chromaStep$')).last
            function.wait_for(state='visible')
            function.hover()
            tooltip = scope.locator('.cm-tooltip').filter(
                has=page.locator('.ide-hover,.embedded-editor-hover'))
            tooltip.wait_for(state='visible')
            self.assertIn('chromaStep', tooltip.text_content())
            colors['hover'] = self.readable(tooltip, '--editor-tooltip', name + ' hover')
            self.evidence(page, 'chrome-hover-' + name, colors)
            self.assertEqual(self.source(page, name), SOURCE)
            observed[name] = colors
        self.assertEqual(observed['main'], observed['embedded'])
        self.assertEqual(observed['main'], observed['standalone'])
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self.app.get_project('embedded')['version'], 1)
        self.assertEqual(self.external, [])


if __name__ == '__main__':
    unittest.main()
