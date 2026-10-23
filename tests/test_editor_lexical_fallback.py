"""Shared temporary colors while actual backend analysis is still pending."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

import test_editor_assistance_ui as fixture

ROOT=Path(__file__).resolve().parents[1]
BASELINE='fn transform(value:Int)->Int{value+1};transform(41)'
PENDING='let transform=41;\n// pending\nlet text="first\n    second";transform+42'


@unittest.skipUnless(shutil.which('node'),'Node.js unavailable')
class SharedLexicalContract(unittest.TestCase):
    def test_standalone_lexer_multiline_numbers_names_and_indentation(self):
        result=subprocess.run([shutil.which('node'),str(ROOT/'tests/editor_lexical_controls.cjs'),str(ROOT)],
            cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        report=json.loads(result.stdout)
        self.assertTrue(report['passed']);self.assertEqual(report['checks'],6)
        self.assertTrue(report['sharedLexer']);self.assertTrue(report['standaloneEmbedded'])
        self.assertFalse(report['network']);self.assertFalse(report['dom'])


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class PendingLexicalColorUI(unittest.TestCase):
    setUp=fixture.EditorAssistanceUI.setUp
    tearDown=fixture.EditorAssistanceUI.tearDown

    def test_pending_real_analysis_clears_old_semantics_and_keeps_both_editors_readable(self):
        from playwright.sync_api import Error
        observed={}
        for name,embedded in [('main',False),('embedded',True)]:
            if embedded: scope=fixture.EditorAssistanceUI._embedded(self)
            else:
                self.page.goto(self.origin+'/?project=assist')
                self.page.wait_for_function('()=>window.ChaosIDE?.editor()&&project?.id==="assist"')
                scope=self.page.locator('#sourceView')
            fixture.EditorAssistanceUI._initial_document(self,BASELINE,embedded=embedded)
            prefix='chaos-token-'if embedded else'ide-token-'
            scope.locator('.'+prefix+'function',has_text='transform').first.wait_for(state='attached')
            held=[]
            def hold(route):
                if route.request.post_data_json.get('source')!=BASELINE:held.append(route)
                else:route.continue_()
            self.page.route('**/api/language/analyze',hold)
            try:
                fixture.EditorAssistanceUI._initial_document(self,PENDING[:-1],embedded=embedded)
                with self.page.expect_request(lambda request:request.method=='POST'
                        and '/api/language/analyze'in request.url
                        and request.post_data_json.get('source')==PENDING):
                    self.page.keyboard.type('2')
                # Pump the intercepted route callback without awaiting the held response.
                self.page.wait_for_timeout(20)
                self.assertTrue(held,'actual analysis request must remain pending')
                self.assertEqual(scope.locator('[class*="ide-token-"],[class*="chaos-token-"]').count(),0)
                colors={}
                for role,text in [('number','42'),('operator','+'),('punctuation',';'),('comment','// pending'),('string','    second"')]:
                    span=scope.locator('.cm-line span').filter(has_text=re.compile('^'+re.escape(text)+'$')).first
                    span.wait_for(state='attached')
                    actual=span.evaluate('el=>getComputedStyle(el).color')
                    expected=scope.evaluate("(el,role)=>{const probe=document.createElement('i');probe.style.color='var(--syntax-'+role+')';el.append(probe);const value=getComputedStyle(probe).color;probe.remove();return value}",role)
                    self.assertEqual(actual,expected,(name,role))
                    self.assertEqual(span.evaluate('el=>getComputedStyle(el).fontStyle'),'normal')
                    colors[role]=actual
                first=scope.locator('.cm-line span').filter(has_text=re.compile('^"first$')).first
                self.assertEqual(first.evaluate('el=>getComputedStyle(el).color'),colors['string'])
                observed[name]=colors
                response=self.app.analyze(PENDING,files={'lib/math.chaos':fixture.LIBRARY},position=len(PENDING))
                for pending in held:
                    try:pending.fulfill(status=200,content_type='application/json',body=json.dumps(response))
                    except Error:pass  # Superseded requests may already be aborted.
            finally:
                self.page.unroute('**/api/language/analyze',hold)
                for pending in held:
                    try:pending.abort()
                    except Error:pass
            # A fresh request must color the changed binding as a variable,
            # rather than resurrecting the previous function role.
            self.page.keyboard.press('ArrowLeft');self.page.keyboard.press('ArrowRight')
            scope.locator('.'+prefix+'variable',has_text='transform').first.wait_for(state='attached')
            self.assertEqual(scope.locator('.'+prefix+'function',has_text='transform').count(),0)
            self.assertEqual(self.app.get_project('embedded'if embedded else'assist')['version'],1)
        self.assertEqual(observed['main'],observed['embedded'])


if __name__=='__main__':unittest.main()
