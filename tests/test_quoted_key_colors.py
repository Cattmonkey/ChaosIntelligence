"""Static AST labels and actual shared colors, without executing edited code."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from chaos.ide_language import analyze
from chaos.language import Evaluator, tokenize
import test_editor_assistance_ui as editor_fixture
import test_semantic_color_ui as color_fixture


ROOT = Path(__file__).resolve().parents[1]
MODES = ('dark', 'paper', 'contrast')
QUOTED = r'''let detail={"caption":"caption",'nested':{"路径🙂":"路径🙂"},"escaped\"key":"escaped\"key"};detail'''
SOURCE = color_fixture.SOURCE + ';\n' + QUOTED
BASELINE = 'fn transform(value:Int)->Int{value+1};transform(41)'
PENDING = ('// held lexer\nlet label="value";let value=1;\n'
           'value ^ ~2;value ? 3 : 4;\nlet copy=[..[1]];\n1.answer')


def token_text(source, token):
    """The public analysis protocol uses UTF-16, including astral characters."""
    raw = source.encode('utf-16-le')
    return raw[token['from'] * 2:token['to'] * 2].decode('utf-16-le')


class QuotedKeySemantics(unittest.TestCase):
    def analysis(self, source):
        with ExitStack() as stack:
            for name in ('run', 'evaluate', 'invoke', '_s_invoke', '_s_evaluate'):
                stack.enter_context(patch.object(Evaluator, name,
                    side_effect=AssertionError('Editor analysis executed source')))
            result = analyze(source)
        self.assertTrue(result['complete'], result['diagnostics'])
        self.assertEqual(result['diagnostics'], [])
        previous = 0
        for token in result['tokens']:
            self.assertGreaterEqual(token['from'], previous)
            self.assertGreater(token['to'], token['from'])
            self.assertTrue(token_text(source, token))
            previous = token['to']
        return result

    def roles(self, source, result, text):
        return [token['type'] for token in result['tokens']
                if token_text(source, token) == text]

    def test_quotes_escapes_unicode_and_nested_values_have_exact_distinct_roles(self):
        source = ('// 🙂 preceding UTF-16 offset\n' +
                  r'''let record={"alpha":"alpha",'beta':'beta',"escaped\"key":"escaped\"key","路径🙂":{"inner":"inner"},plain:"plain"};record''')
        result = self.analysis(source)
        for text in ('"alpha"', "'beta'", r'"escaped\"key"', '"inner"'):
            with self.subTest(text=text):
                self.assertEqual(self.roles(source, result, text), ['property', 'string'])
        self.assertEqual(self.roles(source, result, '"路径🙂"'), ['property'])
        self.assertEqual(self.roles(source, result, 'plain'), ['property'])
        self.assertEqual(self.roles(source, result, '"plain"'), ['string'])

    def test_record_and_class_patterns_color_labels_without_recoloring_bindings(self):
        source = ('class Box{var answer=42};'
                  'let {"outer":{\'nested\':local},..rest}={"outer":{\'nested\':41},"tail":"tail"};'
                  'let Box::{"answer":item,..extra}=Box();[local,item,rest,extra]')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, '"outer"'), ['property', 'property'])
        self.assertEqual(self.roles(source, result, "'nested'"), ['property', 'property'])
        self.assertEqual(self.roles(source, result, '"tail"'), ['property', 'string'])
        self.assertEqual(self.roles(source, result, '"answer"'), ['property'])
        for name in ('local', 'rest', 'item', 'extra'):
            with self.subTest(name=name):
                self.assertEqual(self.roles(source, result, name), ['variable', 'variable'])
        self.assertEqual(set(self.roles(source, result, 'Box')), {'type'})

    def test_comments_subscripts_and_effectful_calls_never_gain_label_semantics(self):
        source = ('// {"tag":"tag"} is only a comment\n'
                  'fn observe(input:Record){let value="tag";let record={"tag":value};'
                  'tool("must.not.run",record);[record["tag"],value,"tag"]};'
                  'observe({"tag":"tag"})')
        result = self.analysis(source)
        self.assertEqual(self.roles(source, result, '"tag"'),
                         ['string', 'property', 'string', 'string', 'property', 'string'])
        comments = [token for token in result['tokens'] if token['type'] == 'comment']
        self.assertEqual(len(comments), 1)
        self.assertIn('{"tag":"tag"}', token_text(source, comments[0]))
        self.assertEqual(self.roles(source, result, '"must.not.run"'), ['string'])
        # This is the actual compiler lexer contract underlying temporary colors.
        tokens = [(token.kind, token.value) for token in
                  tokenize('1.answer ^ ~value ? value : value; [..value]')
                  if token.kind != 'eof']
        self.assertEqual(tokens[:3], [('number', '1'), ('op', '.'), ('id', 'answer')])
        for symbol in ('^', '~', '?', '..'):
            self.assertIn(('op', symbol), tokens)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class QuotedKeyColorUI(unittest.TestCase):
    # Import fixture methods explicitly, rather than inheriting its test IDs.
    setUp = editor_fixture.EditorAssistanceUI.setUp
    tearDown = editor_fixture.EditorAssistanceUI.tearDown
    backend = color_fixture.SemanticColorUI.backend
    main = color_fixture.SemanticColorUI.main
    embedded = color_fixture.SemanticColorUI.embedded
    reader = color_fixture.SemanticColorUI.reader
    background = color_fixture.SemanticColorUI.background
    assert_readable = color_fixture.SemanticColorUI.assert_readable

    def evidence(self, page, label, observed):
        folder = os.environ.get('CHAOS_QUOTED_KEY_AUDIT')
        if not folder:
            return
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / (label + '.png')), full_page=True)
        report = {'observed': observed, 'sourceRoot': str(ROOT),
                  'errors': self.errors, 'externalHTTP': self.external,
                  'sourceHashes': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                   for path in ('chaos/ide_language.py', 'web/theme.css',
                                                'web/code.js', 'web/editor.js', 'web/ide.js')}}
        (target / (label + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')

    def colors(self, page, scope, prefix, analysis, source):
        """Match actual DOM spans against the public UTF-16 token protocol."""
        result = {}
        for role in sorted(color_fixture.ROLES):
            tokens = [token_text(source, token) for token in analysis['tokens']
                      if token['type'] == role]
            spans = scope.locator('.' + prefix + role)
            spans.first.wait_for(state='attached')
            samples = spans.evaluate_all('''els=>els.map(el=>{
              const inner=[...el.querySelectorAll('span')].pop()||el;
              return {text:el.textContent,color:getComputedStyle(inner).color};
            })''')
            self.assertTrue(samples, (role, prefix))
            self.assertTrue(all(sample['text'] in tokens for sample in samples),
                            (role, samples, tokens))
            colors = {sample['color'] for sample in samples}
            self.assertEqual(len(colors), 1, (role, samples))
            result[role] = colors.pop()
        return result

    def test_real_quoted_keys_and_values_across_three_palettes_and_three_surfaces(self):
        analysis = self.backend(SOURCE)
        for text in ('"caption"', '"路径🙂"', r'"escaped\"key"'):
            self.assertEqual([token['type'] for token in analysis['tokens']
                              if token_text(SOURCE, token) == text], ['property', 'string'])
        references = {}
        for name, prepare in (('main', self.main), ('embedded', self.embedded), ('reader', self.reader)):
            page, scope, prefix = prepare(SOURCE)
            for mode in MODES:
                page.evaluate('id=>ChaosCodeAppearance.set(id)', mode)
                page.wait_for_function('id=>ChaosCodeAppearance.get()===id&&document.documentElement.dataset.codePalette===id', arg=mode)
                actual = self.colors(page, scope, prefix, analysis, SOURCE)
                if mode in references:
                    self.assertEqual(actual, references[mode], (name, mode))
                else:
                    references[mode] = actual
                self.assertNotEqual(actual['property'], actual['string'], (name, mode))
                self.assertNotEqual(actual['parameter'], actual['function'], (name, mode))
                self.assertNotEqual(actual['parameter'], actual['variable'], (name, mode))
                for text in ('"caption"', '"路径🙂"', r'"escaped\"key"'):
                    for role in ('property', 'string'):
                        spans = scope.locator('.' + prefix + role).filter(has_text=re.compile('^' + re.escape(text) + '$'))
                        self.assertEqual(spans.count(), 1, (name, mode, role, text))
                        observed = spans.evaluate_all('els=>els.map(el=>getComputedStyle([...el.querySelectorAll("span")].pop()||el).color)')
                        self.assertEqual(observed, [actual[role]], (name, mode, text, role))
                normal = self.background(scope if name == 'reader' else scope.locator('.cm-editor'))
                checks = {'normal': self.assert_readable(actual, normal, name + mode)}
                active = scope.locator('[aria-current="step"]' if name == 'reader' else '.cm-activeLine').first
                active.wait_for(state='attached')
                checks['active'] = self.assert_readable(actual, self.background(active), name + mode + ' active')
                if name != 'reader':
                    page.evaluate('''embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();e.dispatch({selection:{anchor:0,head:e.state.doc.length}});e.focus()}''', name == 'embedded')
                    selected = scope.locator('.cm-selectionBackground').first
                    selected.wait_for(state='attached')
                    background = color_fixture.over(color_fixture.rgba(selected.evaluate('el=>getComputedStyle(el).backgroundColor')), normal)
                    checks['selection'] = self.assert_readable(actual, background, name + mode + ' selection')
                else:
                    page.evaluate('''()=>{const range=document.createRange();range.selectNodeContents(document.querySelector('pre.chaos-code code'));const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}''')
                    selected = scope.locator('.chaos-token-property').first
                    selection_color = color_fixture.rgba(selected.evaluate('el=>getComputedStyle(el,"::selection").backgroundColor'))
                    self.assertGreater(selection_color[3], 0)
                    checks['selection'] = self.assert_readable(actual, color_fixture.over(selection_color, normal), name + mode + ' selection')
                self.evidence(page, 'quoted-' + name + '-' + mode,
                              {'palette': mode, 'surface': name, 'colors': actual,
                               'contrast': checks, 'backendTokens': analysis['tokens']})
            current = (page.evaluate('ChaosCode.source(colorReader)') if name == 'reader'
                       else editor_fixture.EditorAssistanceUI._source(self, embedded=name == 'embedded'))
            self.assertEqual(current, SOURCE)
        self.assertEqual(self.app.get_project('assist')['version'], 1)
        self.assertEqual(self.app.get_project('embedded')['version'], 1)

    def test_held_analysis_keeps_real_operator_and_numeric_member_boundaries(self):
        from playwright.sync_api import Error
        observed = {}
        for name, embedded in (('main', False), ('embedded', True)):
            if embedded:
                scope = editor_fixture.EditorAssistanceUI._embedded(self)
            else:
                self.page.goto(self.origin + '/?project=assist')
                self.page.wait_for_function('()=>window.ChaosIDE?.editor()&&project?.id==="assist"')
                scope = self.page.locator('#sourceView')
            editor_fixture.EditorAssistanceUI._initial_document(self, BASELINE, embedded=embedded)
            prefix = 'chaos-token-' if embedded else 'ide-token-'
            scope.locator('.' + prefix + 'function', has_text='transform').first.wait_for(state='attached')
            held = []
            def hold(route):
                if route.request.post_data_json.get('source') == PENDING:
                    held.append(route)
                else:
                    route.continue_()
            self.page.route('**/api/language/analyze', hold)
            try:
                editor_fixture.EditorAssistanceUI._initial_document(self, PENDING[:-1], embedded=embedded)
                with self.page.expect_request(lambda request: request.method == 'POST'
                        and '/api/language/analyze' in request.url
                        and request.post_data_json.get('source') == PENDING):
                    self.page.keyboard.type(PENDING[-1])
                self.page.wait_for_timeout(20)
                self.assertTrue(held, 'The real backend request must still be held')
                self.assertEqual(scope.locator('[class*="ide-token-"],[class*="chaos-token-"]').count(), 0)
                colors = {}
                for text in ('^', '~', '?', '..'):
                    span = scope.locator('.cm-line span').filter(has_text=re.compile('^' + re.escape(text) + '$')).first
                    span.wait_for(state='attached')
                    actual = span.evaluate('el=>getComputedStyle(el).color')
                    expected = scope.evaluate('''el=>{const p=document.createElement('i');p.style.color='var(--syntax-operator)';el.append(p);const value=getComputedStyle(p).color;p.remove();return value}''')
                    self.assertEqual(actual, expected, (name, text))
                    colors[text] = actual
                member = scope.locator('.cm-line').filter(has_text=re.compile(r'^1\.answer$'))
                self.assertEqual(member.count(), 1)
                for text, role in (('1', 'number'), ('.', 'punctuation')):
                    span = member.locator('span').filter(has_text=re.compile('^' + re.escape(text) + '$'))
                    self.assertEqual(span.count(), 1, (name, text, member.inner_html()))
                    actual = span.evaluate('el=>getComputedStyle(el).color')
                    expected = scope.evaluate('''(el,role)=>{const p=document.createElement('i');p.style.color='var(--syntax-'+role+')';el.append(p);const value=getComputedStyle(p).color;p.remove();return value}''', role)
                    self.assertEqual(actual, expected, (name, text, role))
                    colors[text] = actual
                self.evidence(self.page, 'quoted-pending-' + name, {'heldRequests': len(held), 'colors': colors})
                observed[name] = colors
            finally:
                self.page.unroute('**/api/language/analyze', hold)
                for route in held:
                    try:
                        route.abort()
                    except Error:
                        pass
            self.assertEqual(editor_fixture.EditorAssistanceUI._source(self, embedded=embedded), PENDING)
            self.assertEqual(self.app.get_project('embedded' if embedded else 'assist')['version'], 1)
        self.assertEqual(observed['main'], observed['embedded'])


if __name__ == '__main__':
    unittest.main()
