"""Actual OpenAI SDK transport + declared tool calls through IDE HTTP routes."""
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from chaos import Framework, Client, Settings
from chaos.server import make_server


@unittest.skipUnless(importlib.util.find_spec('openai'),'OpenAI SDK is not installed')
class IDEProviderTransport(unittest.TestCase):
    def test_real_sdk_http_tool_roundtrip_proposal_and_apply(self):
        seen=[]
        class Provider(BaseHTTPRequestHandler):
            def log_message(self,*a): pass
            def do_GET(self):
                raw=json.dumps({'object':'list','data':[{'id':'gpt-6-sol','object':'model'}]}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def do_POST(self):
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));seen.append({'path':self.path,'auth':self.headers.get('Authorization'),'request':request})
                tools={t['function']['description'].split(':',1)[0]:t['function']['name']for t in request['tools']}
                tool_messages=[m for m in request['messages']if m['role']=='tool']
                if not tool_messages:actions=[('draft.read',{'path':'main.chaos'})]
                elif len(tool_messages)==1:actions=[('draft.write',{'path':'main.chaos','source':'fn answer(base:Int=40){base+2};answer()'}),('draft.validate',{}),('draft.evaluate',{})]
                else:actions=[]
                message={'role':'assistant','content':None if actions else '已生成 answer，纯语言检查得到42；等待应用。'}
                if actions:message['tool_calls']=[{'id':'call'+str(len(tool_messages))+'_'+str(i),'type':'function','function':{'name':tools[n],'arguments':json.dumps(v)}}for i,(n,v)in enumerate(actions)]
                result={'id':'fixture-request-'+str(len(seen)),'object':'chat.completion','created':1,'model':request['model'],'choices':[{'index':0,'message':message,'finish_reason':'tool_calls'if actions else'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
                raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider);worker=threading.Thread(target=provider.serve_forever,daemon=True);worker.start()
        try:
            with tempfile.TemporaryDirectory()as tmp:
                settings=Settings(':memory:');settings.update({'providers':{'text':{'baseURL':'http://127.0.0.1:'+str(provider.server_port)+'/v1','apiKey':'local-fixture-key'}},'runtime':{'timeout':15}},1)
                with Framework(settings=settings)as app:
                    server=make_server(port=0,framework=app,state_dir=Path(tmp)/'control',update_home=Path(tmp)/'updates')
                    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                    try:
                        client=Client('http://127.0.0.1:'+str(server.server_port));project=client.create_project('coding',source='input')
                        began=time.monotonic();job=client.start_assistant('coding',project['version'],'把程序改成返回42并检查');self.assertLess(time.monotonic()-began,2)
                        deadline=time.monotonic()+10
                        while job['status']in ('queued','running')and time.monotonic()<deadline:
                            time.sleep(.03);job=client.assistant('coding',job['id'])
                        self.assertEqual(job['status'],'completed',job)
                        self.assertTrue(job['validation']['ok']);self.assertEqual(job['checks'][0]['output'],42)
                        self.assertEqual(client.get_project('coding')['source'],'input')
                        changed=client.apply_assistant('coding',job['id'],project['version'])
                        self.assertEqual(client.start('coding',changed['version'],None,mode='source').result(),42)
                        self.assertEqual(len(client.assistant_history('coding')),1)
                        self.assertTrue(client.assistant_events('coding',job['id'])['items'])
                        self.assertEqual(len(seen),3);self.assertTrue(all(x['path']=='/v1/chat/completions'and x['auth']=='Bearer local-fixture-key'for x in seen))
                        self.assertNotIn('local-fixture-key',json.dumps(job))
                    finally:server.shutdown();server.server_close();thread.join(2)
        finally:provider.shutdown();provider.server_close();worker.join(2)
