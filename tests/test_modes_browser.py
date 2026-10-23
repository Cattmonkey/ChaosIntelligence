"""Real browser effects remain in one language run and replay in a new run."""
import importlib.util
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from chaos import Framework,Settings

@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright extra unavailable')
class LanguageBrowserModes(unittest.TestCase):
    def test_record_then_replay_new_session_with_new_input(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body=b'<title>Modes fixture</title><input data-testid="query"><button data-testid="submit" onclick="document.querySelector(\'output\').textContent=document.querySelector(\'input\').value">Submit</button><output></output>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                settings=Settings(':memory:');settings.update({'runtime':{'workspace':folder}},version=settings.public()['version'])
                with Framework(Path(folder)/'store.sqlite',settings=settings) as app:
                    source='''fn action(url){
 tool("browser.control",{action:"navigate",url:url});
 tool("browser.control",{action:"type",locator:{kind:"testId",value:"query"},text:"first actual"});
 tool("browser.control",{action:"click",locator:{kind:"testId",value:"submit"}})
}
if input.record {
 let capture=await record_skill(action,input.url,[{name:"query",step:1,path:["text"]}]);
 await save_mode(capture.skill,"browser_skill");capture.output
}else{let s=await load_mode("browser_skill");(await replay_skill(s,{query:input.query}))[2]}'''
                    p=app.create_project('browser_mode',source=source,capabilities=['browser.control']);url='http://127.0.0.1:'+str(server.server_port)
                    first=app.start(p['id'],p['version'],{'record':True,'url':url},mode='source').wait(timeout=20)
                    if first['status']=='failed' and "Executable doesn't exist" in first.get('error',''):self.skipTest('Chromium unavailable')
                    self.assertEqual(first['status'],'completed',first.get('error'));self.assertIn('first actual',first['output']['snapshot'])
                    artifact=app.mode(p['id'],'browser_skill');actions=artifact['definition']['actions'];self.assertEqual(len(actions),3)
                    self.assertNotIn('ref',actions[1]['input']);self.assertEqual(actions[1]['input']['locator'],{'kind':'testId','value':'query'})
                    second=app.start(p['id'],p['version'],{'record':False,'query':'second actual'},mode='source').result(timeout=20)
                    self.assertIn('second actual',second['snapshot']);self.assertNotEqual(first['output']['sessionId'],second['sessionId'])
                    events=app.events(p['id'],limit=300)['items'];self.assertEqual(sum(e['type']=='tool.completed'for e in events),6)
        finally:server.shutdown();thread.join(2);server.server_close()

if __name__=='__main__':unittest.main()
