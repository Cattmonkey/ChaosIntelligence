"""Readable views keep executable sources exact, using the real language service."""
import importlib.util,json
from pathlib import Path
import tempfile,threading,unittest
from chaos import Framework,Settings
from chaos.code_presentation import render_code
from chaos.server import make_server

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class CodeReadabilityUI(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.tmp=tempfile.TemporaryDirectory(prefix='chaos-reader-');root=Path(self.tmp.name)
        self.app=Framework(root/'db',settings=Settings(':memory:'));self.server=make_server('127.0.0.1',0,framework=self.app,state_dir=root/'control',update_home=root/'updates')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.pw=sync_playwright().start();self.browser=self.pw.chromium.launch(headless=True);self.context=self.browser.new_context(permissions=['clipboard-read','clipboard-write']);self.page=self.context.new_page();self.errors=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)));self.page.goto(self.origin+'/code.css');self.page.set_content('<!doctype html><main id="host"></main>')
        self.page.add_style_tag(url=self.origin+'/code.css');self.page.add_script_tag(url=self.origin+'/code.js')
    def tearDown(self):
        try:self.assertEqual(self.errors,[])
        finally:self.browser.close();self.pw.stop();self.server.shutdown();self.thread.join(3);self.server.server_close();self.app.close();self.tmp.cleanup()
    def render(self,source,options=None):
        self.page.evaluate('([source,options])=>{window.reader=ChaosCode.render(source,options);document.querySelector("#host").replaceChildren(reader);return reader.ready;}',[source,options or {}])
    def test_dynamic_format_hints_unicode_original_copy_and_bundle_snapshot(self):
        source='// 🙂 原文\nimport "lib.chaos" as L;let channel_send=42;let alias=L.work;alias(channel_send)';lib='fn work(value:Int)->Int{value};export{work}'
        requests=[];self.page.on('request',lambda req:requests.append((req.url,req.post_data_json)) if '/api/language/' in req.url else None)
        self.page.evaluate('([source,lib])=>{window.options={file:"main.chaos",entry:"main.chaos",files:{"main.chaos":source,"lib.chaos":lib},languageVersions:{"main.chaos":2,"lib.chaos":2}};reader=ChaosCode.render(source,options);document.querySelector("#host").append(reader);options.files["lib.chaos"]="999";return reader.ready;}',[source,lib])
        self.assertEqual(self.page.locator('code').text_content(),source);self.page.locator('code [title]',has_text='channel_send').last.click();self.assertIn('channel_send: Int',self.page.locator('.chaos-code-hint-text').text_content())
        self.page.locator('pre').focus();self.page.keyboard.press('Escape');self.assertTrue(self.page.locator('.chaos-code-hint').is_hidden());self.page.keyboard.press('ArrowRight');self.assertTrue(self.page.locator('.chaos-code-hint').is_visible())
        self.page.get_by_role('button',name='下一项',exact=True).click();self.assertTrue(self.page.locator('.chaos-code-hint-text').text_content().startswith('2/'));self.page.keyboard.press('Escape');self.assertTrue(self.page.locator('.chaos-code-help').evaluate('el=>el===document.activeElement'))
        self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.page.wait_for_function('()=>reader.dataset.codeView==="formatted"&&reader.dataset.highlight==="ready"')
        displayed=self.page.locator('code').text_content();self.assertNotEqual(displayed,source);self.assertIn('let channel_send = 42;',displayed);self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),source)
        self.page.locator('.copy-code').click();self.assertEqual(self.page.evaluate('navigator.clipboard.readText()'),source)
        self.page.evaluate('ChaosCode.enhance(reader)');self.assertEqual(self.page.evaluate('ChaosCode.source(reader)'),source)
        self.page.get_by_role('button',name='原始排版',exact=True).click();self.assertEqual(self.page.locator('code').text_content(),source)
        count=len(requests);self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.assertEqual(len(requests),count)
        body=next(body for url,body in requests if url.endswith('/analyze') and body['source']==displayed);self.assertEqual(body['files']['lib.chaos'],lib);self.assertEqual(body['files']['main.chaos'],displayed);self.assertEqual(body['languageVersions']['main.chaos'],2)
    def test_offline_formatting_keyboard_and_safe_hint_text(self):
        source='// 🙂\nfn work(value:Int)->Int{value+1};work(41)';self.page.locator('#host').evaluate('(host,html)=>host.innerHTML=html',render_code(source,'chaos',readable=True));self.page.evaluate('()=>{reader=document.querySelector("pre");ChaosCode.enhance(reader)}')
        requests=[];self.page.on('request',lambda req:requests.append(req.url));self.context.set_offline(True)
        self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.assertEqual(self.page.locator('pre').get_attribute('data-code-view'),'formatted');self.assertNotEqual(self.page.locator('code').text_content(),source)
        self.page.locator('pre').focus();self.page.keyboard.press('ArrowRight');self.assertIn('work(value: Int) -> Int',self.page.locator('.chaos-code-hint-text').text_content());self.page.keyboard.press('Escape');self.assertTrue(self.page.locator('.chaos-code-hint').is_hidden())
        self.page.get_by_role('button',name='原始排版',exact=True).click();self.assertEqual(self.page.locator('code').text_content(),source);self.assertEqual(requests,[]);self.context.set_offline(False)
        unsafe='bad "><img src=x onerror=alert(1)>';self.page.locator('code [title]').first.evaluate('(token,hint)=>token.title=hint',unsafe);self.page.locator('code [title]').first.click();self.assertIn(unsafe,self.page.locator('.chaos-code-hint-text').text_content());self.assertEqual(self.page.locator('img').count(),0)
    def test_no_format_for_debug_disabled_large_nonchaos_or_invalid_offline(self):
        for options in ({'activeLine':1},{'analyze':False},{'language':'python'}):
            self.render('let x=1;x',options);self.assertEqual(self.page.locator('.chaos-code-format').count(),0)
        self.render('a'*100001);self.assertEqual(self.page.locator('.chaos-code-format').count(),0)
        self.page.locator('#host').evaluate('(host,html)=>host.innerHTML=html',render_code('fn broken(','chaos',readable=True));self.page.evaluate('ChaosCode.enhance(document.querySelector("pre"))');self.assertEqual(self.page.locator('.chaos-code-format').count(),0)
        self.render('fn broken(');self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.page.wait_for_function('()=>reader.querySelector(".chaos-code-status").textContent.includes("保留原始源码")');self.assertEqual(self.page.locator('code').text_content(),'fn broken(')
    def test_late_format_and_analysis_cannot_replace_source_or_debug_lines(self):
        from playwright.sync_api import Error
        held=[];self.render('let old=1;old');self.page.route('**/api/language/format-source',lambda route:held.append(route));self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.page.wait_for_timeout(60);self.assertEqual(len(held),1)
        self.page.evaluate('ChaosCode.update(reader,"43",{activeLine:1}).ready')
        try:held[0].fulfill(status=200,content_type='application/json',body=json.dumps({'source':'999\n'}))
        except Error:pass
        self.page.unroute('**/api/language/format-source');self.assertEqual(self.page.locator('code').text_content(),'43');self.assertEqual(self.page.locator('[aria-current="step"]').get_attribute('data-line'),'1')
        analyses=[]
        def hold(route):
            if route.request.post_data_json.get('source')=='let slow = 1;\nslow\n':analyses.append(route)
            else:route.continue_()
        self.page.route('**/api/language/analyze',hold);self.page.evaluate('ChaosCode.update(reader,"let slow=1;slow").ready');self.page.get_by_role('button',name='格式化阅读',exact=True).click();self.page.wait_for_function('()=>reader.dataset.codeView==="formatted"');self.page.wait_for_timeout(60);self.assertEqual(len(analyses),1)
        # Returning to original does not await semantic analysis.
        self.page.get_by_role('button',name='原始排版',exact=True).click();self.assertEqual(self.page.locator('code').text_content(),'let slow=1;slow')
        self.page.evaluate('ChaosCode.update(reader,"44",{activeLine:1}).ready')
        try:analyses[0].fulfill(status=200,content_type='application/json',body=json.dumps({'tokens':[],'symbols':[]}))
        except Error:pass
        self.assertEqual(self.page.locator('code').text_content(),'44');self.assertEqual(self.page.locator('[aria-current="step"]').count(),1)
    def test_embedded_editor_lexical_colors_survive_service_outage(self):
        self.page.add_script_tag(url=self.origin+'/vendor/codemirror.js');self.page.add_script_tag(url=self.origin+'/editor.js');self.page.route('**/api/language/analyze',lambda route:route.fulfill(status=503,body='{"error":"offline"}',content_type='application/json'))
        self.page.evaluate('source=>{const t=document.createElement("textarea");t.value=source;document.querySelector("#host").append(t);ChaosEditor.mount(t)}', '// readable comment\nlet text="hello"')
        self.page.wait_for_function('()=>document.querySelector(".embedded-editor-status").textContent.includes("语言服务暂不可用")')
        spans=self.page.locator('.cm-content span').all();comments=[s for s in spans if 'readable comment' in s.text_content()];strings=[s for s in spans if 'hello' in s.text_content()];self.assertTrue(comments);self.assertTrue(strings)
        self.assertNotEqual(comments[0].evaluate('el=>getComputedStyle(el).color'),strings[0].evaluate('el=>getComputedStyle(el).color'));self.assertGreaterEqual(comments[0].evaluate('el=>{const parse=s=>{const n=s.match(/[\\d.]+/g).map(Number);return [n[0],n[1],n[2],n.length>3?n[3]:1]},blend=(f,b)=>f.slice(0,3).map((v,i)=>v*f[3]+b[i]*(1-f[3])),lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);let chain=[],p=el;while(p){chain.push(parse(getComputedStyle(p).backgroundColor));p=p.parentElement}let bg=[255,255,255];for(const layer of chain.reverse())bg=blend(layer,bg);const fg=blend(parse(getComputedStyle(el).color),bg),a=lum(fg),b=lum(bg);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)}'),4.5);self.assertGreaterEqual(strings[0].evaluate('el=>{const parse=s=>{const n=s.match(/[\\d.]+/g).map(Number);return [n[0],n[1],n[2],n.length>3?n[3]:1]},blend=(f,b)=>f.slice(0,3).map((v,i)=>v*f[3]+b[i]*(1-f[3])),lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);let chain=[],p=el;while(p){chain.push(parse(getComputedStyle(p).backgroundColor));p=p.parentElement}let bg=[255,255,255];for(const layer of chain.reverse())bg=blend(layer,bg);const fg=blend(parse(getComputedStyle(el).color),bg),a=lum(fg),b=lum(bg);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)}'),4.5)
if __name__=='__main__':unittest.main()
