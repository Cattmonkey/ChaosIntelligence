"""Computed colors from the real backend analysis on isolated editor surfaces.

These tests launch only their own temporary Framework/server/browser fixture.
The author does not execute this proposal. No existing browser or project is used.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import unittest

import test_editor_assistance_ui as fixture

ROOT=Path(__file__).resolve().parent.parent
SOURCE='// quiet readable comment\nfn transform(value:Int)->Int{value+1};\nlet channel_send=transform(41);let label="steady";\nlet record={answer:channel_send};record.answer'
ROLES={'keyword','string','number','comment','operator','punctuation','variable','function','parameter','type','property'}


def rgba(value):
    match=re.fullmatch(r'rgba?\(([^)]+)\)',value)
    if not match:raise AssertionError('Unexpected computed color '+value)
    parts=[float(part.strip()) for part in match.group(1).split(',')]
    return tuple(parts[:3])+((parts[3] if len(parts)==4 else 1),)


def over(foreground,background):
    alpha=foreground[3]
    return tuple(foreground[i]*alpha+background[i]*(1-alpha) for i in range(3))+(1,)


def luminance(color):
    channels=[c/255 for c in color[:3]]
    return sum(weight*(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4)
               for c,weight in zip(channels,(.2126,.7152,.0722)))


def contrast(foreground,background):
    one,two=sorted((luminance(over(rgba(foreground),background)),luminance(background)))
    return (two+.05)/(one+.05)


@unittest.skipUnless(fixture.importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class SemanticColorUI(unittest.TestCase):
    # Reuse the established isolated fixture, not its test methods or test IDs.
    setUp=fixture.EditorAssistanceUI.setUp
    tearDown=fixture.EditorAssistanceUI.tearDown

    def backend(self,source):
        response=self.page.request.post(self.origin+'/api/language/analyze',data={
            'source':source,'file':'main.chaos','entry':'main.chaos',
            'files':{'main.chaos':source},'languageVersions':{'main.chaos':2},'position':len(source)})
        self.assertTrue(response.ok,response.text())
        result=response.json();self.assertTrue(result['complete']);self.assertEqual(result['diagnostics'],[])
        self.assertTrue(ROLES<={token['type'] for token in result['tokens']})
        return result

    def main(self,source):
        self.page.goto(self.origin+'/?project=assist')
        self.page.wait_for_function('()=>window.ChaosIDE?.editor()&&project?.id==="assist"')
        fixture.EditorAssistanceUI._initial_document(self,source)
        return self.page,self.page.locator('#sourceView'),'ide-token-'

    def embedded(self,source):
        box=fixture.EditorAssistanceUI._embedded(self)
        fixture.EditorAssistanceUI._initial_document(self,source,embedded=True)
        return self.page,box,'chaos-token-'

    def reader(self,source):
        page=self.context.new_page();page.on('pageerror',lambda error:self.errors.append(str(error)))
        page.goto(self.origin+'/code.css');page.set_content('<!doctype html><main id="host"></main>')
        page.add_style_tag(url=self.origin+'/code.css');page.add_script_tag(url=self.origin+'/code.js')
        page.evaluate("source=>{window.colorReader=ChaosCode.render(source,{file:'main.chaos',entry:'main.chaos',files:{'main.chaos':source},languageVersions:{'main.chaos':2},activeLine:2});document.querySelector('#host').append(colorReader);return colorReader.ready}",source)
        return page,page.locator('pre.chaos-code'),'chaos-token-'

    def colors(self,page,scope,prefix,analysis,source):
        result={}
        for role in sorted(ROLES):
            tokens=[source[t['from']:t['to']] for t in analysis['tokens'] if t['type']==role]
            spans=scope.locator('.'+prefix+role)
            spans.first.wait_for(state='attached')
            samples=spans.evaluate_all("els=>els.map(el=>{const inner=[...el.querySelectorAll('span')].pop()||el;return {text:el.textContent,color:getComputedStyle(inner).color}})")
            self.assertTrue(samples,(role,prefix))
            self.assertTrue(all(sample['text'] in tokens for sample in samples),(role,samples,tokens))
            colors={sample['color']for sample in samples}
            self.assertEqual(len(colors),1,(role,samples))
            result[role]=colors.pop()
        return result

    def background(self,locator):
        layers=locator.evaluate("el=>{const layers=[];for(let n=el;n;n=n.parentElement)layers.push(getComputedStyle(n).backgroundColor);return layers.reverse()}")
        result=(255,255,255,1)
        for layer in layers:result=over(rgba(layer),result)
        return result

    def assert_readable(self,colors,background,label):
        ratios={role:contrast(color,background) for role,color in colors.items()}
        self.assertTrue(all(value>=4.5 for value in ratios.values()),(label,background,ratios))
        return ratios

    def evidence(self,label,page,data):
        folder=os.environ.get('CHAOS_SEMANTIC_COLOR_AUDIT')
        if not folder:return
        target=Path(folder);target.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(target/(label+'.png')),full_page=True)
        data.update({'sourceRoot':str(ROOT),'origin':self.origin,'errors':self.errors,'externalHTTP':self.external,
          'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in
          ('web/theme.css','web/code.css','web/editor.css','web/ide.css','web/editor.js','web/ide.js','web/code.js','chaos/ide_language.py')}})
        (target/(label+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

    def test_backend_roles_share_computed_colors_across_main_embedded_and_reader(self):
        analysis=self.backend(SOURCE);reference=None;observed={}
        for name,prepare in [('main',self.main),('embedded',self.embedded),('reader',self.reader)]:
            page,scope,prefix=prepare(SOURCE)
            colors=self.colors(page,scope,prefix,analysis,SOURCE)
            if reference is None:reference=colors
            else:self.assertEqual(colors,reference,name)
            for role in ('function','parameter','type','property'):
                self.assertNotEqual(colors[role],colors['variable'],(name,role))
            self.assertNotEqual(colors['comment'],colors['variable'])
            self.assertLess(luminance(rgba(colors['comment'])),luminance(rgba(colors['variable'])),name)
            normal=self.background(scope.locator('.cm-editor')if name!='reader'else scope)
            checks={'normal':self.assert_readable(colors,normal,name+' normal')}
            if name!='reader':
                active=scope.locator('.cm-activeLine').first
                active.wait_for(state='attached');self.assertGreater(active.count(),0,name+' active line missing')
                checks['active']=self.assert_readable(colors,self.background(active),name+' active')
                page.evaluate("embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();e.dispatch({selection:{anchor:0,head:e.state.doc.length}});e.focus()}",name=='embedded')
                selection=scope.locator('.cm-selectionBackground').first;selection.wait_for(state='attached')
                selected=over(rgba(selection.evaluate('el=>getComputedStyle(el).backgroundColor')),normal)
                checks['selection']=self.assert_readable(colors,selected,name+' selected')
            else:
                active=scope.locator('[aria-current="step"]');self.assertEqual(active.count(),1)
                checks['active']=self.assert_readable(colors,self.background(active),name+' active')
            observed[name]={'colors':colors,'contrast':checks}
            self.evidence('semantic-colors-'+name,page,{'status':'passed','backendTokens':analysis['tokens'],'surface':name,'observed':observed[name]})
        # A native-looking identifier is colored as the real local variable.
        shadowed=[t for t in analysis['tokens']if SOURCE[t['from']:t['to']]=='channel_send']
        self.assertTrue(shadowed);self.assertEqual({t['type']for t in shadowed},{'variable'})
        self.assertNotEqual(reference['variable'],reference['function'])
        # Renaming arbitrary declarations preserves role colors through fresh real analysis.
        renamed=SOURCE.replace('transform','combine').replace('channel_send','work_item').replace('value','argument')
        fresh=self.backend(renamed);page,scope,prefix=self.main(renamed)
        self.assertEqual(self.colors(page,scope,prefix,fresh,renamed),reference)
        self.assertEqual(self.app.get_project('assist')['version'],1)
        self.assertEqual(self.app.get_project('embedded')['version'],1)

    def test_print_colors_remain_readable_for_real_backend_roles_on_every_surface(self):
        analysis=self.backend(SOURCE)
        for name,prepare in [('main',self.main),('embedded',self.embedded),('reader',self.reader)]:
            page,scope,prefix=prepare(SOURCE)
            self.colors(page,scope,prefix,analysis,SOURCE)
            page.emulate_media(media='print')
            try:
                colors=self.colors(page,scope,prefix,analysis,SOURCE)
                background=self.background(scope.locator('.cm-editor')if name!='reader'else scope)
                ratios=self.assert_readable(colors,background,name+' print')
                self.evidence('semantic-colors-print-'+name,page,{'status':'passed','surface':name,'media':'print','colors':colors,'background':background,'contrast':ratios,'backendTokens':analysis['tokens']})
            finally:page.emulate_media(media='screen')


if __name__=='__main__':unittest.main()
