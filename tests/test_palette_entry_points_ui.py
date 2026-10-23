"""Visible shared palette entry points on isolated editors and real documentation.

Only this fixture's temporary server, projects and headless browser are used.
The author writes source only; root runs actual acceptance after independent review.
"""
from contextlib import ExitStack
import copy,hashlib,importlib.util,json,os
from pathlib import Path
import sys,unittest
from unittest.mock import patch
from chaos.language import Evaluator
import test_editor_assistance_ui as editor_fixture
import test_semantic_color_ui as color_fixture
import test_code_palette_preview_ui as preview_fixture
from workbench_actions import command as workbench_command
ROOT=Path(__file__).resolve().parents[1]
SOURCE=color_fixture.SOURCE
MODES=('paper','contrast','dark')
BUTTON='button[aria-controls="codeAppearanceDialog"][aria-haspopup="dialog"]'
LABELS={'dark':'暗色','paper':'纸白','contrast':'高对比'}

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class PaletteEntryPointsUI(unittest.TestCase):
    setUp=editor_fixture.EditorAssistanceUI.setUp
    tearDown=editor_fixture.EditorAssistanceUI.tearDown
    backend=color_fixture.SemanticColorUI.backend
    colors=color_fixture.SemanticColorUI.colors
    background=color_fixture.SemanticColorUI.background
    assert_readable=color_fixture.SemanticColorUI.assert_readable

    def extra_page(self):
        page=self.context.new_page();page.set_default_timeout(10000)
        page.on('pageerror',lambda error:self.errors.append(str(error)))
        return page

    def snapshot(self,page,embedded=False):
        return page.evaluate("embedded=>{const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();return {source:e.state.doc.toString(),selection:e.state.selection.toJSON()}}",embedded)

    def projects(self):
        return {name:{key:copy.deepcopy(value)for key,value in self.app.get_project(name).items()if key in ('id','version','source','files','graph')}for name in ('assist','embedded')}

    def guard_execution(self,stack):
        return [stack.enter_context(patch.object(Evaluator,name,side_effect=AssertionError('Palette control executed source')))for name in ('run','evaluate','invoke','_s_invoke','_s_evaluate')]

    def watch(self,page):
        previews=[];mutations=[]
        def observe(request):
            if preview_fixture.preview_request(request):previews.append(request)
            elif request.method!='GET'and'/api/'in request.url and'/api/language/'not in request.url:mutations.append((request.method,request.url))
        page.on('request',observe);return previews,mutations

    def ready(self,page):
        dialog=page.locator('#codeAppearanceDialog');dialog.wait_for(state='visible')
        page.wait_for_function('()=>document.querySelector("#codeAppearancePreview pre.chaos-code")?.dataset.highlight==="ready"')
        self.assertEqual(page.locator('#codeAppearanceDialog').count(),1)
        self.assertEqual(dialog.locator('input[name="codeAppearance"]').count(),3)
        self.assertEqual(dialog.locator('#codeAppearancePreview pre.chaos-code').count(),1)
        self.assertEqual(dialog.locator('#codeAppearancePreview '+BUTTON+':visible').count(),0)
        self.assertEqual(dialog.locator('#codeAppearancePreview .chaos-code-toolbar:visible').count(),0)
        return dialog

    def choose(self,page,mode):
        dialog=self.ready(page);dialog.locator('input[name="codeAppearance"][value="'+mode+'"]').check()
        page.wait_for_function('mode=>ChaosCodeAppearance.get()===mode&&document.documentElement.dataset.codePalette===mode',arg=mode)
        return dialog

    def close(self,page,trigger=None):
        page.get_by_role('button',name='关闭代码配色',exact=True).click()
        page.locator('#codeAppearanceDialog').wait_for(state='hidden')
        if trigger is not None:
            trigger.evaluate('el=>{window.paletteExpectedFocus192=el}')
            page.wait_for_function('()=>document.activeElement===window.paletteExpectedFocus192')

    def pump_close(self,page):
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')

    def token_color(self,scope,prefix,role):
        token=scope.locator('.'+prefix+role).first;token.wait_for(state='attached')
        return token.evaluate('el=>getComputedStyle([...el.querySelectorAll("span")].pop()||el).color')

    def variable(self,scope,name):
        return scope.evaluate("(el,name)=>{const probe=document.createElement('i');probe.style.color='var('+name+')';el.append(probe);const color=getComputedStyle(probe).color;probe.remove();return color}",name)

    def evidence(self,page,label,observed):
        folder=os.environ.get('CHAOS_APPEARANCE_ENTRY_AUDIT')
        if not folder:return
        target=Path(folder);target.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(target/(label+'.png')),full_page=True)
        (target/(label+'.json')).write_text(json.dumps({'observed':observed,'errors':self.errors,'externalRequests':self.external,'sourceHashes':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()for name in ('web/code.js','web/code.css','web/editor.js','web/workbench.js')}},ensure_ascii=False,indent=2)+'\n')

    def test_embedded_visible_palette_shares_main_theme_cache_and_preserves_history(self):
        page=self.page;projects=self.projects()
        page.evaluate('()=>{const e=ChaosIDE.editor();e.dispatch({selection:{anchor:e.state.doc.length}});e.focus()}')
        page.keyboard.insert_text(' // palette-entry')
        page.evaluate('()=>ChaosIDE.editor().dispatch({selection:{anchor:1,head:4}})')
        main_state=self.snapshot(page);self.assertEqual(main_state['source'],editor_fixture.INITIAL+' // palette-entry')
        embedded=self.extra_page();saved=self.page
        try:
            self.page=embedded;box=editor_fixture.EditorAssistanceUI._embedded(self)
            editor_fixture.EditorAssistanceUI._initial_document(self,SOURCE,embedded=True)
        finally:self.page=saved
        analysis=self.backend(SOURCE);self.colors(embedded,box,'chaos-token-',analysis,SOURCE)
        embedded_state=self.snapshot(embedded,True)
        trigger=box.locator('.embedded-editor-toolbar '+BUTTON)
        self.assertEqual(trigger.count(),1);self.assertTrue(trigger.is_visible())
        self.assertEqual(trigger.inner_text(),'配色 · 暗色')
        previews,mutations=self.watch(embedded);main_previews,main_mutations=self.watch(page)
        self.assertEqual(embedded.locator('#codeAppearancePreview pre.chaos-code').count(),0)
        with ExitStack()as stack:
            guards=self.guard_execution(stack)
            trigger.click();self.ready(embedded)
            embedded.evaluate('()=>{window.entryDialog192=document.getElementById("codeAppearanceDialog");window.entryPreview192=document.querySelector("#codeAppearancePreview pre")}')
            self.assertEqual(len(previews),1)
            observed={}
            for index,mode in enumerate(MODES):
                if index:trigger.click()
                self.choose(embedded,mode)
                page.wait_for_function('mode=>ChaosCodeAppearance.get()===mode&&document.documentElement.dataset.codePalette===mode',arg=mode)
                page.wait_for_function('label=>document.getElementById("codeAppearanceButton").textContent===label',arg='配色 · '+LABELS[mode])
                self.assertEqual(trigger.inner_text(),'配色 · '+LABELS[mode])
                actual=self.colors(embedded,box,'chaos-token-',analysis,SOURCE)
                self.assert_readable(actual,self.background(box.locator('.cm-editor')),mode+' embedded')
                self.assertEqual(actual['number'],self.token_color(page.locator('#sourceView'),'ide-token-','number'))
                self.assertEqual(self.snapshot(page),main_state);self.assertEqual(self.snapshot(embedded,True),embedded_state)
                self.assertTrue(embedded.evaluate('()=>document.getElementById("codeAppearanceDialog")===window.entryDialog192&&document.querySelector("#codeAppearancePreview pre")===window.entryPreview192'))
                self.close(embedded,trigger);self.assertEqual(len(previews),1)
                observed[mode]={'colors':actual,'dialogCount':embedded.locator('#codeAppearanceDialog').count(),'previewRequests':len(previews)}
            self.assertEqual(mutations+main_mutations,[]);self.assertTrue(all(item.call_count==0 for item in guards));self.assertEqual(main_previews,[])
        page.bring_to_front();page.evaluate('()=>ChaosIDE.editor().focus()')
        page.keyboard.press('Meta+z'if sys.platform=='darwin'else'Control+z')
        page.wait_for_function('text=>ChaosIDE.editor().state.doc.toString()===text',arg=editor_fixture.INITIAL)
        page.keyboard.press('Meta+Shift+z'if sys.platform=='darwin'else'Control+Shift+z')
        page.wait_for_function('text=>ChaosIDE.editor().state.doc.toString()===text',arg=main_state['source'])
        self.assertEqual(self.projects(),projects)
        self.evidence(embedded,'embedded-entry',{'modes':observed,'mainLogicalSelectionAndDraftPreserved':True,'embeddedLogicalSelectionAndDraftPreserved':True,'undoRedoPreserved':True,'mutations':mutations+main_mutations})

    def test_actual_docs_reader_and_command_palette_expose_the_same_shared_control(self):
        page=self.page;projects=self.projects();main_state=self.snapshot(page)
        docs=self.extra_page();docs.goto(self.origin+'/docs#/generic-functions')
        docs.locator('#article-content pre.chaos-code').first.wait_for(state='visible')
        reader=docs.locator('#article-content pre.chaos-code').first
        trigger=reader.locator('.chaos-code-toolbar '+BUTTON)
        self.assertEqual(trigger.count(),1);self.assertTrue(trigger.is_visible());self.assertEqual(trigger.inner_text(),'配色 · 暗色')
        self.assertEqual(docs.locator('#formatSource').count(),0)
        original=reader.evaluate('el=>ChaosCode.source(el)');previews,mutations=self.watch(docs);main_previews,main_mutations=self.watch(page)
        with ExitStack()as stack:
            guards=self.guard_execution(stack)
            trigger.click();self.choose(docs,'paper');self.close(docs,trigger)
            self.assertEqual(len(previews),1);self.assertEqual(reader.evaluate('el=>ChaosCode.source(el)'),original)
            page.wait_for_function('()=>ChaosCodeAppearance.get()==="paper"')
            function_color=self.variable(page.locator('#sourceView .cm-editor'),'--syntax-function')
            self.assertEqual(self.token_color(reader,'chaos-token-','function'),function_color)
            self.assertGreaterEqual(color_fixture.contrast(function_color,self.background(reader)),4.5)
            trigger.click();self.ready(docs);self.assertEqual(len(previews),1);self.close(docs,trigger)
            # Public command-menu interaction, not a hidden DOM button or direct open().
            page.bring_to_front();command=page.evaluate('()=>ChaosWorkbench.commands.find(item=>item.id==="edit.appearance")')
            self.assertIsNotNone(command);self.assertEqual(command['label'],'编辑：代码配色')
            workbench_command(page,'edit.appearance');self.choose(page,'contrast')
            self.assertEqual(page.locator('#codeAppearanceDialog').count(),1)
            self.assertEqual(len(main_previews),1)
            self.close(page,page.locator('#wbCommandButton'))
            docs.wait_for_function('()=>ChaosCodeAppearance.get()==="contrast"')
            self.assertEqual(trigger.inner_text(),'配色 · 高对比')
            self.assertEqual(reader.evaluate('el=>ChaosCode.source(el)'),original)
            self.assertEqual(self.snapshot(page),main_state);self.assertEqual(mutations+main_mutations,[]);self.assertTrue(all(item.call_count==0 for item in guards))
        self.assertEqual(self.projects(),projects)
        self.evidence(docs,'docs-reader-entry',{'readerSourcePreserved':True,'previewRequests':len(previews),'mainPreviewRequests':len(main_previews),'command':command,'mutations':mutations+main_mutations})

    def test_removed_reader_trigger_does_not_steal_focus_and_new_reader_reuses_dialog(self):
        page,reader,_=color_fixture.SemanticColorUI.reader(self,SOURCE)
        reader=page.locator('#host > pre.chaos-code')
        # This is a true independent read-only page: no IDE host exists.
        self.assertEqual(page.locator('#formatSource').count(),0)
        trigger=reader.locator('.chaos-code-toolbar '+BUTTON);self.assertEqual(trigger.count(),1);self.assertTrue(trigger.is_visible())
        previews,mutations=self.watch(page);projects=self.projects()
        with ExitStack()as stack:
            guards=self.guard_execution(stack)
            trigger.click();self.ready(page)
            page.evaluate('()=>{window.singletonDialog192=document.getElementById("codeAppearanceDialog");window.singletonPreview192=document.querySelector("#codeAppearancePreview pre")}')
            trigger.evaluate('el=>el.remove()')
            self.close(page)
            # A modal may reject outside focus. Focus the surviving reader only
            # after real close, then pump DOM events to expose late focus theft.
            reader.focus();self.pump_close(page)
            self.assertTrue(reader.evaluate('el=>document.activeElement===el'))
            self.assertEqual(page.locator('#codeAppearanceDialog').count(),1);self.assertEqual(len(previews),1)
            page.evaluate("source=>{const pre=ChaosCode.render(source,{file:'second.chaos',languageVersion:2});pre.id='secondPaletteReader192';document.querySelector('#host').append(pre);return pre.ready}",SOURCE)
            second=page.locator('#secondPaletteReader192');next_trigger=second.locator('.chaos-code-toolbar '+BUTTON)
            self.assertEqual(next_trigger.count(),1);self.assertTrue(next_trigger.is_visible())
            next_trigger.click();self.choose(page,'paper')
            self.assertTrue(page.evaluate('()=>document.getElementById("codeAppearanceDialog")===window.singletonDialog192&&document.querySelector("#codeAppearancePreview pre")===window.singletonPreview192'))
            self.close(page,next_trigger);self.assertEqual(len(previews),1)
            self.assertEqual(mutations,[]);self.assertTrue(all(item.call_count==0 for item in guards))
        self.assertEqual(self.projects(),projects)
        self.evidence(page,'removed-trigger',{'focusStayedOnSurvivingReader':True,'singletonDialog':True,'singletonCachedPreview':True,'previewRequests':len(previews),'mutations':mutations})

    def test_reader_in_existing_native_dialog_opens_palette_and_restores_outer_focus(self):
        page,reader,_=color_fixture.SemanticColorUI.reader(self,SOURCE)
        page.evaluate("""()=>{
          const dialog=document.createElement('dialog');dialog.id='outerReaderDialog192';
          const title=document.createElement('h2');title.textContent='只读代码示例';
          dialog.append(title,document.querySelector('#host pre.chaos-code'));
          document.body.append(dialog);dialog.showModal();
        }""")
        outer=page.locator('#outerReaderDialog192');outer.wait_for(state='visible')
        reader=outer.locator('pre.chaos-code')
        trigger=outer.locator('.chaos-code-toolbar '+BUTTON)
        self.assertEqual(trigger.count(),1);self.assertTrue(trigger.is_visible())
        original=reader.evaluate('el=>ChaosCode.source(el)');projects=self.projects()
        previews,mutations=self.watch(page)
        self.assertEqual(page.locator('#codeAppearanceDialog').count(),0)
        with ExitStack()as stack:
            guards=self.guard_execution(stack)
            trigger.click();self.choose(page,'paper')
            self.assertTrue(outer.evaluate('el=>el.open'))
            self.assertTrue(page.locator('#codeAppearanceDialog').evaluate('el=>el.open'))
            self.assertEqual(page.locator('#codeAppearanceDialog').count(),1)
            self.assertEqual(len(previews),1)
            self.assertEqual(reader.evaluate('el=>ChaosCode.source(el)'),original)
            self.close(page,trigger)
            self.assertTrue(outer.evaluate('el=>el.open'))
            self.assertTrue(trigger.evaluate('el=>document.activeElement===el'))
            self.assertEqual(reader.evaluate('el=>ChaosCode.source(el)'),original)
            trigger.click();self.ready(page);self.assertEqual(len(previews),1)
            self.close(page,trigger)
            self.assertTrue(outer.evaluate('el=>el.open'))
            self.assertEqual(page.locator('#codeAppearanceDialog').count(),1)
            self.assertEqual(mutations,[]);self.assertTrue(all(item.call_count==0 for item in guards))
        self.assertEqual(self.projects(),projects)
        self.evidence(page,'native-dialog-reader-entry',{'outerDialogStillOpen':True,'returnFocusToInnerReaderEntry':True,'singletonDialog':True,'previewRequests':len(previews),'readerSourcePreserved':True,'mutations':mutations})
