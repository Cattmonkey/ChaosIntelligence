"""Real signature help follows shared code palettes without editing user code."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

import test_editor_assistance_ui as fixture
import test_semantic_color_ui as color_fixture
from workbench_actions import command as workbench_command

SOURCE='fn combine(first:Int,second:Int)->Int{first+second};combine(40,2)'
POSITION=SOURCE.rindex('2)')
MODES=('dark','paper','contrast')
ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class SignatureAppearanceUI(unittest.TestCase):
    setUp=fixture.EditorAssistanceUI.setUp
    tearDown=fixture.EditorAssistanceUI.tearDown
    main=color_fixture.SemanticColorUI.main
    embedded=color_fixture.SemanticColorUI.embedded
    background=color_fixture.SemanticColorUI.background

    def mode(self,page,value):
        page.evaluate('id=>ChaosCodeAppearance.set(id)',value)
        page.wait_for_function('id=>ChaosCodeAppearance.get()===id&&document.documentElement.dataset.codePalette===id',arg=value)

    def value(self,locator,name):
        return locator.evaluate('''(el,name)=>{const p=document.createElement('i');p.style.color='var('+name+')';el.append(p);const value=getComputedStyle(p).color;p.remove();return value}''',name)

    def evidence(self,page,label,data):
        folder=os.environ.get('CHAOS_SIGNATURE_APPEARANCE_AUDIT')or os.environ.get('CHAOS_APPEARANCE_AUDIT')
        if not folder:return
        target=Path(folder);target.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(target/(label+'.png')),full_page=True)
        (target/(label+'.json')).write_text(json.dumps({'observed':data,'errors':self.errors,'externalRequests':self.external,'sourceHashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()for n in ('web/theme.css','web/code.js','web/workbench.js')}},indent=2)+'\n')

    def test_actual_active_parameter_panels_follow_three_palettes_on_both_editors(self):
        response=self.page.request.post(self.origin+'/api/language/analyze',data={'source':SOURCE,'position':POSITION,'file':'main.chaos','entry':'main.chaos','files':{'main.chaos':SOURCE},'languageVersions':{'main.chaos':2}})
        self.assertTrue(response.ok,response.text());analysis=response.json()
        self.assertEqual(analysis['diagnostics'],[]);self.assertEqual(analysis['signatureHelp']['activeParameter'],1)
        self.assertEqual([p['name']for p in analysis['signatureHelp']['parameters']],['first','second'])
        observed={}
        for name,prepare in (('main',self.main),('embedded',self.embedded)):
            page,scope,prefix=prepare(SOURCE);embedded=name=='embedded'
            page.evaluate('''({embedded,position})=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();e.dispatch({selection:{anchor:position}});e.focus()}''',{'embedded':embedded,'position':POSITION})
            if embedded:scope.get_by_role('button',name='参数提示',exact=True).click()
            else:workbench_command(page,'edit.signature')
            panel=scope.locator('.embedded-editor-signature'if embedded else '#ideSignatureHelp')
            panel.wait_for(state='visible')
            chips=panel.locator('.embedded-editor-parameters code'if embedded else '.ide-signature-parameter')
            self.assertEqual(chips.count(),2);active=panel.locator('[aria-current="true"]')
            self.assertEqual(active.count(),1);self.assertIn('second',active.inner_text())
            cm=scope.locator('.cm-editor');body_background=page.locator('body').evaluate('el=>getComputedStyle(el).backgroundColor')
            initial=page.evaluate('''embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}''',embedded)
            for mode in MODES:
                self.mode(page,mode)
                expected_background=self.value(cm,'--panel2'if embedded else'--panel')
                self.assertEqual(panel.evaluate('el=>getComputedStyle(el).backgroundColor'),expected_background,(name,mode))
                color=active.evaluate('el=>getComputedStyle(el).color');parameter=self.value(cm,'--syntax-parameter')
                self.assertEqual(color,parameter,(name,mode));self.assertEqual(self.value(panel,'--syntax-parameter'),parameter)
                border=active.evaluate('el=>{const s=getComputedStyle(el);return {color:s.borderTopColor,width:parseFloat(s.borderTopWidth),style:s.borderTopStyle,weight:parseInt(s.fontWeight,10)}}')
                self.assertEqual(border['color'],parameter);self.assertGreater(border['width'],0);self.assertNotEqual(border['style'],'none');self.assertGreaterEqual(border['weight'],600)
                ratios=[]
                for chip in chips.all():
                    foreground=chip.evaluate('el=>getComputedStyle(el).color');ratio=color_fixture.contrast(foreground,self.background(chip))
                    self.assertGreaterEqual(ratio,4.5,(name,mode,chip.inner_text(),foreground,ratio));ratios.append(ratio)
                self.assertEqual(page.locator('body').evaluate('el=>getComputedStyle(el).backgroundColor'),body_background)
                self.assertEqual(page.evaluate('''embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}''',embedded),initial)
                observed[name+':'+mode]={'background':expected_background,'activeParameterColor':color,'border':border,'contrast':ratios}
                self.evidence(page,'signature-'+name+'-'+mode,observed[name+':'+mode])
        self.assertEqual(self.app.get_project('assist')['version'],1);self.assertEqual(self.app.get_project('embedded')['version'],1)

    def test_palette_button_stays_next_to_format_after_workbench_move_and_preserves_undo(self):
        page=self.page
        page.wait_for_function('''()=>{const f=document.getElementById('formatSource'),b=document.getElementById('codeAppearanceButton');return f&&b&&f.nextElementSibling===b}''')
        button=page.locator('#codeAppearanceButton');self.assertTrue(button.is_visible())
        self.assertFalse(button.evaluate('el=>!!el.closest(".ide-editor-status")'))
        page.evaluate('()=>{const e=ChaosIDE.editor();e.dispatch({selection:{anchor:e.state.doc.length}});e.focus()}')
        page.keyboard.insert_text(' // appearance-check')
        page.evaluate('()=>ChaosIDE.editor().dispatch({selection:{anchor:1,head:4}})')
        original=page.evaluate('()=>{const e=ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}')
        self.assertEqual(original['source'],fixture.INITIAL+' // appearance-check')
        for mode in MODES:
            button.click();dialog=page.locator('#codeAppearanceDialog');dialog.wait_for(state='visible')
            dialog.locator('input[name="codeAppearance"][value="'+mode+'"]').check()
            page.keyboard.press('Escape');dialog.wait_for(state='hidden')
            self.assertEqual(page.evaluate('ChaosCodeAppearance.get()'),mode)
            self.assertEqual(page.evaluate('()=>{const e=ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}'),original)
            self.assertTrue(button.evaluate('el=>el.previousElementSibling?.id==="formatSource"'))
        page.evaluate('()=>ChaosIDE.editor().focus()')
        page.keyboard.press('Meta+z'if sys.platform=='darwin'else'Control+z')
        page.wait_for_function('text=>ChaosIDE.editor().state.doc.toString()===text',arg=fixture.INITIAL)
        page.keyboard.press('Meta+Shift+z'if sys.platform=='darwin'else'Control+Shift+z')
        page.wait_for_function('text=>ChaosIDE.editor().state.doc.toString()===text',arg=original['source'])
        self.assertEqual(self.app.get_project('assist')['version'],1)
        self.assertEqual(self.app.get_project('assist')['source'],fixture.INITIAL)
        self.evidence(page,'signature-palette-toolbar',{'adjacentToFormat':True,'sourceSelectionPreserved':True,'undoRedoPreserved':True,'currentSource':original['source']})


if __name__=='__main__':unittest.main()
