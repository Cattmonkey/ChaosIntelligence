"""Actual browser coverage for source and assistant panels at narrow widths."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from chaos import Framework, Settings
from chaos.server import make_server


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright extra unavailable')
class ResponsiveIDE(unittest.TestCase):
    def test_mobile_resize_panels_preserve_draft_and_run(self):
        from playwright.sync_api import sync_playwright
        source='fn add(left:Int,right:Int)->Int{left+right};add(20,22)'
        with tempfile.TemporaryDirectory(prefix='chaos-responsive-') as folder, Framework(Path(folder)/'runtime.sqlite',settings=Settings(':memory:')) as app:
            app.create_project('responsive',source=source,capabilities=[])
            server=make_server('127.0.0.1',0,framework=app,state_dir=Path(folder)/'control',update_home=Path(folder)/'updates')
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin='http://127.0.0.1:'+str(server.server_port);errors=[];external=[]
            try:
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    try:
                        context=browser.new_context(viewport={'width':390,'height':900})
                        def route(request):
                            if request.request.url.startswith(origin+'/'):request.continue_()
                            else:external.append(request.request.url);request.abort()
                        context.route('http**://**',route)
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/?project=responsive');page.locator('.cm-editor').wait_for(state='attached');page.wait_for_function("()=>document.querySelector('#sourceView').classList.contains('active')")
                        content=page.locator('.cm-content[contenteditable="true"]')
                        def shot(name):
                            output=os.environ.get('CHAOS_RESPONSIVE_AUDIT')
                            if output:Path(output).mkdir(parents=True,exist_ok=True);page.screenshot(path=str(Path(output)/(name+'.png')),full_page=True)
                        def visible_editor():
                            self.assertTrue(page.locator('.ide-editor').is_visible());self.assertFalse(page.locator('.ide-assistant').is_visible())
                            # The 48px activity rail is intentionally retained on phones.
                            rail=page.locator('#wbActivity').bounding_box()['width']
                            self.assertGreaterEqual(page.locator('.ide-editor').bounding_box()['width'],390-rail-16)
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                            self.assertTrue(page.locator('.cm-content').evaluate('el=>{const r=el.getBoundingClientRect();const top=Math.max(r.top,0),bottom=Math.min(r.bottom,innerHeight);return bottom>top&&el.contains(document.elementFromPoint(r.left+Math.min(40,r.width/2),(top+bottom)/2))}'))
                        def command(name):
                            page.keyboard.press('ControlOrMeta+Shift+P');page.locator('#wbCommandInput').fill(name)
                            label=page.evaluate('(id)=>ChaosWorkbench.commands.find(c=>c.id===id).label',name)
                            page.locator('#wbCommandList [role="option"]').filter(has_text=label).click();page.locator('#wbCommands').wait_for(state='hidden')
                        visible_editor();shot('initial-mobile')
                        page.locator('#wbActivity [data-command="files"]').click();self.assertTrue(page.locator('.ide-files').is_visible());page.locator('#wbActivity [data-command="files"]').click();self.assertFalse(page.locator('.ide-files').is_visible())
                        draft='fn add(left:Int,right:Int)->Int{left+right};let answer=add(20,22);answer'
                        content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(draft);page.keyboard.press('Home');page.keyboard.press('Shift+ArrowRight')
                        before=page.evaluate('({source:ChaosIDE.editor().state.doc.toString(),selection:ChaosIDE.editor().state.selection.toJSON(),file:activeFile})')
                        page.locator('#wbActivity [data-command="agent"]').click();self.assertFalse(page.locator('.ide-editor').is_visible());self.assertEqual(page.locator('#ideAssistantToggle').get_attribute('aria-expanded'),'true');self.assertTrue(page.locator('#ideChatPrompt').evaluate('el=>el===document.activeElement'))
                        page.locator('#ideChatPrompt').fill('保留这段未发送的需求');shot('mobile-assistant');page.locator('#ideAssistantClose').click();visible_editor();self.assertTrue(page.evaluate('ChaosIDE.editor().hasFocus'));self.assertEqual(page.evaluate('({source:ChaosIDE.editor().state.doc.toString(),selection:ChaosIDE.editor().state.selection.toJSON(),file:activeFile})'),before)
                        page.locator('#wbActivity [data-command="agent"]').click();self.assertEqual(page.locator('#ideChatPrompt').input_value(),'保留这段未发送的需求');page.keyboard.press('Escape');visible_editor();self.assertTrue(page.evaluate('ChaosIDE.editor().hasFocus'))
                        page.set_viewport_size({'width':1500,'height':1050});page.wait_for_function("()=>document.querySelector('#ideAssistantToggle').getAttribute('aria-expanded')==='false'");page.locator('#wbActivity [data-command="agent"]').click();page.wait_for_function("()=>document.querySelector('#ideAssistantToggle').getAttribute('aria-expanded')==='true'");self.assertTrue(page.locator('.ide-editor').is_visible());self.assertTrue(page.locator('.ide-assistant').is_visible());shot('desktop')
                        page.set_viewport_size({'width':390,'height':900});page.wait_for_function("()=>document.querySelector('#ideAssistantToggle').getAttribute('aria-expanded')==='false'");visible_editor();self.assertEqual(page.evaluate('ChaosIDE.editor().state.doc.toString()'),draft)
                        page.set_viewport_size({'width':1500,'height':1050});page.wait_for_function("()=>document.querySelector('#ideAssistantToggle').getAttribute('aria-expanded')==='true'");page.locator('#ideAssistantClose').click();page.set_viewport_size({'width':390,'height':900});visible_editor();page.set_viewport_size({'width':1500,'height':1050});self.assertEqual(page.locator('#ideAssistantToggle').get_attribute('aria-expanded'),'false');page.set_viewport_size({'width':390,'height':900});visible_editor()
                        content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text('channel_info(channel()).');page.keyboard.press('Control+Space');page.locator('.cm-completionLabel').filter(has_text='capacity').click();self.assertTrue(page.evaluate('ChaosIDE.editor().state.doc.toString()').endswith('.capacity'))
                        content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text('fn add(left:Int,right:Int)->Int{left+right};add(20,');command('edit.signature');page.wait_for_function("()=>document.querySelector('#ideSignatureHelp .active')?.textContent.includes('right')");shot('mobile-signature')
                        content.click();page.keyboard.press('ControlOrMeta+A');page.keyboard.insert_text(draft);command('edit.format');page.wait_for_function("()=>ChaosIDE.editor().state.doc.lines>2");page.locator('#saveButton').click();page.wait_for_function('()=>!dirty&&!saveTask');saved=app.get_project('responsive');self.assertGreater(saved['version'],1)
                        page.reload();page.locator('.cm-editor').wait_for(state='attached');page.wait_for_function("()=>document.querySelector('#sourceView').classList.contains('active')");page.wait_for_function('(text)=>ChaosIDE.editor().state.doc.toString()===text',arg=saved['source']);visible_editor();page.locator('#runButton').click();page.wait_for_function("()=>!busy&&!launching&&document.querySelector('#resultPanel')?.textContent.trim()==='42'");self.assertEqual(page.evaluate('runVersion'),saved['version']);page.wait_for_function("()=>document.querySelector('#ideLanguageStatus')?.textContent.includes('分析已更新')");self.assertGreaterEqual(page.locator('.ide-token-function').first.evaluate('el=>{const parse=s=>{const n=s.match(/[\\d.]+/g).map(Number);return [n[0],n[1],n[2],n.length>3?n[3]:1]},blend=(f,b)=>f.slice(0,3).map((v,i)=>v*f[3]+b[i]*(1-f[3])),lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);let chain=[],p=el;while(p){chain.push(parse(getComputedStyle(p).backgroundColor));p=p.parentElement}let bg=[255,255,255];for(const layer of chain.reverse())bg=blend(layer,bg);const fg=blend(parse(getComputedStyle(el).color),bg),a=lum(fg),b=lum(bg);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)}'),4.5);self.assertNotEqual(page.locator('.ide-token-function').first.evaluate('el=>getComputedStyle(el).color'),page.locator('.cm-content').evaluate('el=>getComputedStyle(el).color'));shot('mobile-saved-result')
                        self.assertEqual(errors,[]);self.assertEqual(external,[])
                        output=os.environ.get('CHAOS_RESPONSIVE_AUDIT')
                        if output:
                            root=Path(__file__).resolve().parent.parent
                            evidence={'status':'passed','sourceRoot':str(root),'origin':origin,'savedVersion':saved['version'],'runVersion':page.evaluate('runVersion'),'result':42,'errors':errors,'externalHTTP':external,'draftAndSelectionPreserved':True,'assistantDraftPreserved':True,'mobileEditorHitTest':True,'desktopPreferenceRestored':True,'sourceHashes':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('web/ide.css','web/ide.js','web/index.html','tests/test_ide_responsive.py')}}
                            (Path(output)/'browser-result.json').write_text(json.dumps(evidence,indent=2)+'\n')
                    finally:browser.close()
            finally:server.shutdown();thread.join(3);server.server_close()


if __name__=='__main__':unittest.main()
