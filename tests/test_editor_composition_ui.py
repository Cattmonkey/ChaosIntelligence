"""Real browser synthetic composition contracts; not an OS IME acceptance claim.

Uses the installed CM bundle, native formatter and isolated fixture server. A
composition start/end without document edits must also invalidate held output.
"""
import importlib.util
import time
import unittest
import test_editor_assistance_ui as fixture


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class EditorCompositionUI(unittest.TestCase):
    setUp=fixture.EditorAssistanceUI.setUp
    tearDown=fixture.EditorAssistanceUI.tearDown
    _initial_document=fixture.EditorAssistanceUI._initial_document
    _source=fixture.EditorAssistanceUI._source
    _embedded=fixture.EditorAssistanceUI._embedded

    def _composition(self,embedded,kind):
        content=self.page.locator('.embedded-editor .cm-content'if embedded else'#sourceView .cm-content')
        content.evaluate('(el,kind)=>el.dispatchEvent(new CompositionEvent(kind,{bubbles:true,data:""}))',kind)
        self.page.wait_for_function('''({embedded,active})=>{
          const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
          return e.compositionStarted===active;
        }''',arg={'embedded':embedded,'active':kind=='compositionstart'})

    def _format(self,embedded,operation):
        return self.page.evaluate('''({embedded,operation})=>{
          const api=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')):ChaosIDE;
          return Promise.resolve().then(()=>operation==='save'?api.formatBeforeSave():embedded?api.format():api.formatDraft())
            .then(()=>({status:'ok'}),error=>({status:'error',message:error.message}));
        }''',{'embedded':embedded,'operation':operation})

    def _exercise(self,embedded):
        container=self._embedded()if embedded else self.page.locator('#sourceView')
        raw='let answer=40+2;answer';self._initial_document(raw,embedded=embedded)
        expected=self.app.format_source(raw);self.assertNotEqual(expected,raw)
        requests=[];held=[];hold=[False]
        def route(request):
            requests.append(request.request.post_data_json)
            if hold[0]:held.append(request)
            else:request.continue_()
        self.page.route('**/api/language/format-source',route)
        self._composition(embedded,'compositionstart')
        self.assertFalse(self.page.evaluate('''embedded=>{
          const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
          return e.composing;
        }''',embedded),'early compositionStarted must be protected before composing becomes true')
        container.get_by_role('button',name='格式化',exact=True).click()
        rejected=self._format(embedded,'save')
        self.assertEqual(rejected['status'],'error');self.assertIn('输入法',rejected['message'])
        self.assertEqual(requests,[]);self.assertEqual(self._source(embedded=embedded),raw)
        self.assertEqual(self.app.get_project('embedded'if embedded else'assist')['version'],1)
        self._composition(embedded,'compositionend')

        # This is a real request held at the browser route, not a fabricated
        # formatter result. The native formatter runs when it is released.
        hold[0]=True
        self.page.evaluate('''embedded=>{
          const api=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')):ChaosIDE;
          window.__compositionFormatResult={status:'pending'};
          (embedded?api.format():api.formatDraft()).then(
            ()=>window.__compositionFormatResult={status:'ok'},
            error=>window.__compositionFormatResult={status:'error',message:error.message});
        }''',embedded)
        deadline=time.monotonic()+3
        while not held and time.monotonic()<deadline:self.page.wait_for_timeout(10)
        self.assertEqual(len(held),1)
        self._composition(embedded,'compositionstart');self._composition(embedded,'compositionend')
        self.assertEqual(self._source(embedded=embedded),raw)
        hold[0]=False;held.pop().continue_()
        self.page.wait_for_function('()=>window.__compositionFormatResult.status!=="pending"')
        result=self.page.evaluate('window.__compositionFormatResult')
        self.assertEqual(result['status'],'error');self.assertIn('输入法',result['message'])
        self.assertEqual(self._source(embedded=embedded),raw)
        self.assertEqual(self.app.get_project('embedded'if embedded else'assist')['version'],1)

        # Once composition has ended, both manual and automatic-save entry
        # points use the original formatter and its independent undo boundary.
        self.assertEqual(self._format(embedded,'manual'),{'status':'ok'})
        self.assertEqual(self._source(embedded=embedded),expected)
        self.page.evaluate('''embedded=>{
          const e=embedded?ChaosEditor.get(document.querySelector('textarea.source-field')).editor:ChaosIDE.editor();
          e.focus();
        }''',embedded)
        self.page.keyboard.press('ControlOrMeta+Z')
        self.assertEqual(self._source(embedded=embedded),raw)
        self.assertEqual(self._format(embedded,'save'),{'status':'ok'})
        self.assertEqual(self._source(embedded=embedded),expected)
        self.assertEqual(len(requests),3)
        self.assertEqual(self.app.get_project('embedded'if embedded else'assist')['version'],1)

    def test_main_synthetic_composition_blocks_format_and_stale_response_then_recovers(self):
        self._exercise(False)

    def test_embedded_synthetic_composition_blocks_format_and_stale_response_then_recovers(self):
        self._exercise(True)
