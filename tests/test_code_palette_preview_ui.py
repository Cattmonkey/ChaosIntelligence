"""Lazy palette examples use real analysis and preserve the open user document."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from chaos.language import Evaluator
import test_editor_assistance_ui as editor_fixture
import test_semantic_color_ui as colors_fixture

ROOT=Path(__file__).resolve().parents[1]
MAIN=colors_fixture.SOURCE
PREVIEW='// 智能体也是可组合函数\nfn prepare(input: String) -> String {\n  let result = {answer: input, score: 42};\n  result.answer\n}\nagent analyst {\n  provider: "local", prompt: "提炼要点", tools: []\n}\nanalyst(prepare("任务"))'
MODES=('dark','paper','contrast')


def preview_request(request):
    return request.method=='POST'and request.url.endswith('/api/language/analyze')and request.post_data_json.get('file')=='palette-preview.chaos'


def token_text(source,token):
    data=source.encode('utf-16-le')
    return data[token['from']*2:token['to']*2].decode('utf-16-le')


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class CodePalettePreviewUI(unittest.TestCase):
    setUp=editor_fixture.EditorAssistanceUI.setUp
    tearDown=editor_fixture.EditorAssistanceUI.tearDown
    main=colors_fixture.SemanticColorUI.main
    backend=colors_fixture.SemanticColorUI.backend
    background=colors_fixture.SemanticColorUI.background
    assert_readable=colors_fixture.SemanticColorUI.assert_readable

    def colors(self,page,scope,prefix,analysis,source):
        observed={}
        for role in sorted(colors_fixture.ROLES):
            expected=[token_text(source,t)for t in analysis['tokens']if t['type']==role]
            spans=scope.locator('.'+prefix+role);spans.first.wait_for(state='attached')
            samples=spans.evaluate_all("els=>els.map(el=>{const inner=[...el.querySelectorAll('span')].pop()||el;return {text:el.textContent,color:getComputedStyle(inner).color}})")
            self.assertTrue(samples,role);self.assertTrue(all(s['text']in expected for s in samples),(role,samples,expected))
            values={s['color']for s in samples};self.assertEqual(len(values),1,(role,samples));observed[role]=values.pop()
        return observed

    def prepare(self):
        analysis=self.backend(MAIN);page,scope,_=self.main(MAIN)
        self.colors(page,scope,'ide-token-',analysis,MAIN)
        page.evaluate('()=>{const e=ChaosIDE.editor();e.dispatch({selection:{anchor:3,head:8}})}')
        self.assertEqual(page.locator('#codeAppearancePreview pre.chaos-code').count(),0)
        return analysis,self.state()

    def state(self):
        return self.page.evaluate('()=>{const e=ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}')

    def guard_execution(self,stack):
        guards=[]
        for name in ('run','evaluate','invoke','_s_invoke','_s_evaluate'):
            guards.append(stack.enter_context(patch.object(Evaluator,name,side_effect=AssertionError('Palette preview executed source'))))
        return guards

    def evidence(self,label,data):
        folder=os.environ.get('CHAOS_PALETTE_PREVIEW_AUDIT')
        if not folder:return
        target=Path(folder);target.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(target/(label+'.png')),full_page=True)
        (target/(label+'.json')).write_text(json.dumps({'observed':data,'pageErrors':self.errors,'externalRequests':self.external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('web/code.js','web/theme.css','chaos/ide_language.py')}},ensure_ascii=False,indent=2)+'\n')

    def assert_preserved(self,state):
        self.assertEqual(self.state(),state)
        project=self.app.get_project('assist');self.assertEqual(project['version'],1)
        self.assertEqual(project['files']['main.chaos'],editor_fixture.INITIAL)
        self.assertEqual(self.app.get_project('embedded')['version'],1)

    def test_lazy_real_analyzed_preview_shares_all_roles_and_caches_across_three_palettes(self):
        analysis,state=self.prepare();requests=[];mutations=[]
        self.page.on('request',lambda request:requests.append(request)if preview_request(request)else mutations.append((request.method,request.url))if request.method!='GET'and'/api/'in request.url and'/api/language/'not in request.url else None)
        preview=self.page.locator('#codeAppearancePreview pre.chaos-code')
        with ExitStack()as stack:
            guards=self.guard_execution(stack)
            with self.page.expect_response(lambda response:preview_request(response.request))as received:
                self.page.locator('#codeAppearanceButton').click()
            response=received.value;self.assertTrue(response.ok,response.text());actual=response.json()
            self.assertEqual(actual['diagnostics'],[]);self.assertTrue(actual['complete'])
            self.assertTrue(colors_fixture.ROLES<={t['type']for t in actual['tokens']})
            preview.wait_for(state='visible');self.page.wait_for_function('()=>document.querySelector("#codeAppearancePreview pre")?.dataset.highlight==="ready"')
            self.assertEqual(preview.locator('code').text_content(),PREVIEW)
            self.assertEqual(preview.evaluate('el=>ChaosCode.source(el)'),PREVIEW)
            self.assertEqual(len(requests),1);self.assertEqual(requests[0].post_data_json['source'],PREVIEW)
            self.assertEqual(requests[0].post_data_json['languageVersions'],{'palette-preview.chaos':2})
            functions={token_text(PREVIEW,t)for t in actual['tokens']if t['type']=='function'}
            self.assertTrue({'prepare','analyst'}<=functions,functions)
            self.page.evaluate('()=>{window.previewIdentity188=document.querySelector("#codeAppearancePreview pre")}')
            observed={}
            for mode in MODES:
                self.page.locator('#codeAppearanceDialog input[name="codeAppearance"][value="'+mode+'"]').check()
                self.page.wait_for_function('id=>ChaosCodeAppearance.get()===id',arg=mode)
                preview_colors=self.colors(self.page,preview,'chaos-token-',actual,PREVIEW)
                main_colors=self.colors(self.page,self.page.locator('#sourceView'),'ide-token-',analysis,MAIN)
                self.assertEqual(preview_colors,main_colors,mode)
                ratios=self.assert_readable(preview_colors,self.background(preview),mode+' preview')
                cm=self.page.locator('#sourceView .cm-editor');normal=self.background(cm);auxiliary={}
                for variable in ('--editor-search','--editor-search-selected','--editor-bracket'):
                    actual_background=cm.evaluate('''(el,name)=>{const probe=document.createElement('i');probe.style.backgroundColor='var('+name+')';el.append(probe);const color=getComputedStyle(probe).backgroundColor;probe.remove();return color}''',variable)
                    background=colors_fixture.over(colors_fixture.rgba(actual_background),normal)
                    auxiliary[variable]={'background':actual_background,'contrast':self.assert_readable(main_colors,background,mode+' '+variable)}
                self.assert_preserved(state);self.assertEqual(len(requests),1)
                observed[mode]={'colors':preview_colors,'contrast':ratios,'auxiliaryBackgrounds':auxiliary}
            self.page.get_by_role('button',name='关闭代码配色',exact=True).click()
            self.page.locator('#codeAppearanceButton').click()
            self.page.locator('#codeAppearanceDialog').wait_for(state='visible')
            self.assertTrue(preview.evaluate('el=>el===window.previewIdentity188'))
            self.page.wait_for_timeout(60);self.assertEqual(len(requests),1);self.assert_preserved(state)
            self.assertEqual(mutations,[]);self.assertTrue(all(g.call_count==0 for g in guards))
            self.evidence('palette-preview-ready',{'backendTokens':actual['tokens'],'observed':observed,'previewAnalysisRequests':len(requests),'executionMethodCalls':[g.call_count for g in guards],'unexpectedMutations':mutations})

    def test_failed_preview_keeps_original_example_and_retries_real_analysis_without_execution(self):
        _,state=self.prepare();requests=[];mutations=[]
        self.page.on('request',lambda request:requests.append(request)if preview_request(request)else mutations.append((request.method,request.url))if request.method!='GET'and'/api/'in request.url and'/api/language/'not in request.url else None)
        def unavailable(route):
            if preview_request(route.request):route.fulfill(status=503,content_type='application/json',body='{"error":"preview analysis unavailable"}')
            else:route.continue_()
        self.page.route('**/api/language/analyze',unavailable)
        try:
            with ExitStack()as stack:
                guards=self.guard_execution(stack)
                self.page.locator('#codeAppearanceButton').click()
                self.page.wait_for_function('()=>document.querySelector("#codeAppearancePreview pre")?.dataset.highlight==="unavailable"')
                preview=self.page.locator('#codeAppearancePreview pre.chaos-code')
                self.assertEqual(preview.locator('code').text_content(),PREVIEW);self.assertEqual(preview.evaluate('el=>ChaosCode.source(el)'),PREVIEW)
                self.assertIn('原始示例',self.page.locator('#codeAppearancePreview p[role="status"]').inner_text())
                self.assertEqual(preview.locator('[class*="chaos-token-"]').count(),0)
                self.assertEqual(len(requests),1);self.assert_preserved(state)
                self.evidence('palette-preview-unavailable',{'sourcePreserved':True,'previewAnalysisRequests':len(requests),'unexpectedMutations':mutations})
                self.page.get_by_role('button',name='关闭代码配色',exact=True).click()
                self.page.unroute('**/api/language/analyze',unavailable)
                with self.page.expect_response(lambda response:preview_request(response.request))as received:
                    self.page.locator('#codeAppearanceButton').click()
                self.assertTrue(received.value.ok);actual=received.value.json();self.assertEqual(actual['diagnostics'],[])
                self.page.wait_for_function('()=>document.querySelector("#codeAppearancePreview pre")?.dataset.highlight==="ready"')
                self.assertEqual(preview.locator('code').text_content(),PREVIEW);self.assertEqual(len(requests),2)
                self.page.get_by_role('button',name='关闭代码配色',exact=True).click();self.page.locator('#codeAppearanceButton').click()
                self.page.wait_for_timeout(60);self.assertEqual(len(requests),2);self.assert_preserved(state)
                self.assertEqual(mutations,[]);self.assertTrue(all(g.call_count==0 for g in guards))
                self.evidence('palette-preview-retried',{'backendTokens':actual['tokens'],'previewAnalysisRequests':len(requests),'executionMethodCalls':[g.call_count for g in guards],'unexpectedMutations':mutations})
        finally:self.page.unroute('**/api/language/analyze',unavailable)
